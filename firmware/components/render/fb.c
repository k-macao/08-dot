/**
 * @file fb.c
 * @brief 152x296 单色帧缓冲 + UTF-8 文本渲染实现
 */
#include "fb.h"

#include <stddef.h>
#include <string.h>

#include "font_gen.h"

/* ------------------------------------------------------------------ */
/* 像素                                                                */
/* ------------------------------------------------------------------ */

void fb_clear(fb_t *f)
{
    memset(f->buf, 0xFF, FB_SIZE); /* 1 = 白 */
}

void fb_pixel(fb_t *f, int x, int y, bool black)
{
    if (x < 0 || y < 0 || x >= FB_W || y >= FB_H) {
        return;
    }
    int idx = y * FB_STRIDE + (x >> 3);
    uint8_t mask = (uint8_t)(0x80u >> (x & 7));
    if (black) {
        f->buf[idx] &= (uint8_t)~mask; /* 0 = 黑 */
    } else {
        f->buf[idx] |= mask;
    }
}

/* ------------------------------------------------------------------ */
/* UTF-8                                                               */
/* ------------------------------------------------------------------ */

uint32_t fb_utf8_next(const char **p)
{
    const uint8_t *s = (const uint8_t *)*p;
    if (!s || !*s) {
        return 0;
    }
    uint32_t cp;
    int n;
    if (s[0] < 0x80) {
        cp = s[0];
        n = 1;
    } else if ((s[0] & 0xE0) == 0xC0) {
        cp = s[0] & 0x1Fu;
        n = 2;
    } else if ((s[0] & 0xF0) == 0xE0) {
        cp = s[0] & 0x0Fu;
        n = 3;
    } else if ((s[0] & 0xF8) == 0xF0) {
        cp = s[0] & 0x07u;
        n = 4;
    } else {
        (*p)++;
        return 0xFFFD;
    }
    for (int i = 1; i < n; i++) {
        if ((s[i] & 0xC0) != 0x80) {
            *p = (const char *)(s + 1);
            return 0xFFFD;
        }
    }
    for (int i = 1; i < n; i++) {
        cp = (cp << 6) | (uint32_t)(s[i] & 0x3F);
    }
    *p = (const char *)(s + n);
    return cp;
}

/* ------------------------------------------------------------------ */
/* 字库查找 (码点升序, 二分)                                            */
/* ------------------------------------------------------------------ */

#define GLYPH_MISSING_W 7

static int font_find(uint32_t cp)
{
    if (cp > 0xFFFF) {
        return -1;
    }
    int lo = 0, hi = ZG_FONT_COUNT - 1;
    uint16_t v = (uint16_t)cp;
    while (lo <= hi) {
        int mid = (lo + hi) >> 1;
        uint16_t cur = zg_font_cps[mid];
        if (cur == v) {
            return mid;
        }
        if (cur < v) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return -1;
}

int fb_glyph_width(uint32_t cp)
{
    int idx = font_find(cp);
    if (idx < 0) {
        return GLYPH_MISSING_W;
    }
    return zg_font_widths[idx];
}

static void draw_glyph(fb_t *f, int x, int y_top, int idx, int w, bool bold)
{
    if (idx >= 0) {
        const uint8_t *bits = &zg_font_bits[idx * 32];
        for (int row = 0; row < ZG_FONT_SIZE; row++) {
            int yy = y_top + row;
            if (yy < 0 || yy >= FB_H) {
                continue;
            }
            uint8_t lo = bits[row * 2];
            uint8_t hi = bits[row * 2 + 1];
            for (int col = 0; col < w; col++) {
                uint8_t byte = (col < 8) ? lo : hi;
                int bit = (col < 8) ? col : col - 8;
                if (byte & (uint8_t)(0x80u >> bit)) {
                    fb_pixel(f, x + col, yy, true);
                    if (bold && x + col + 1 < FB_W) {
                        fb_pixel(f, x + col + 1, yy, true);
                    }
                }
            }
        }
    } else {
        /* 缺字占位: 空心小方框 */
        for (int i = 0; i < 5; i++) {
            fb_pixel(f, x + i, y_top + 8, true);
            fb_pixel(f, x + i, y_top + 14, true);
        }
        for (int i = 8; i <= 14; i++) {
            fb_pixel(f, x, y_top + i, true);
            fb_pixel(f, x + 4, y_top + i, true);
        }
    }
}

static int draw_one(fb_t *f, int x, int y_top, uint32_t cp, bool bold)
{
    int w = fb_glyph_width(cp);
    int idx = font_find(cp);
    draw_glyph(f, x, y_top, idx, w, bold);
    return w;
}

/* ------------------------------------------------------------------ */
/* 文本测量与绘制                                                       */
/* ------------------------------------------------------------------ */

int fb_text_width(const char *utf8)
{
    int w = 0;
    const char *p = utf8;
    uint32_t cp;
    while ((cp = fb_utf8_next(&p)) != 0) {
        if (cp == '\n') {
            break;
        }
        w += fb_glyph_width(cp);
    }
    return w;
}

int fb_wrap_count(const char *utf8, int max_w)
{
    if (!utf8 || !*utf8) {
        return 0;
    }
    int lines = 0;
    int cx = 0;
    const char *p = utf8;
    uint32_t cp;
    while ((cp = fb_utf8_next(&p)) != 0) {
        if (cp == '\n') {
            lines++;
            cx = 0;
            continue;
        }
        int w = fb_glyph_width(cp);
        if (cx + w > max_w && cx > 0) {
            lines++;
            cx = 0;
        }
        cx += w;
    }
    if (cx > 0) {
        lines++;
    }
    return lines;
}

int fb_draw_line(fb_t *f, int x, int y, const char *utf8, bool bold)
{
    int start_x = x;
    const char *p = utf8;
    uint32_t cp;
    while ((cp = fb_utf8_next(&p)) != 0) {
        if (cp == '\n') {
            break;
        }
        x += draw_one(f, x, y, cp, bold);
    }
    return x - start_x;
}

static int draw_segment(fb_t *f, int x, int y, const char *s, const char *e,
                        bool bold)
{
    const char *p = s;
    int start = x;
    while (p < e) {
        uint32_t cp = fb_utf8_next(&p);
        if (cp == 0) {
            break;
        }
        x += draw_one(f, x, y, cp, bold);
    }
    return x - start;
}

static int seg_width(const char *s, const char *e)
{
    int w = 0;
    const char *p = s;
    while (p < e) {
        uint32_t cp = fb_utf8_next(&p);
        if (cp == 0) {
            break;
        }
        w += fb_glyph_width(cp);
    }
    return w;
}

/* 绘制 [s,e) 段；center 为真时在 [x, x+max_w) 内居中 */
static void emit_segment(fb_t *f, int x, int y, int max_w, const char *s,
                         const char *e, bool bold, bool center)
{
    if (s >= e) {
        return;
    }
    int seg_w = seg_width(s, e);
    int lx = x;
    if (center && seg_w < max_w) {
        lx = x + (max_w - seg_w) / 2;
    }
    draw_segment(f, lx, y, s, e, bold);
}

int fb_draw_wrapped(fb_t *f, int x, int y, int max_w, const char *utf8,
                    bool bold, bool center, int max_lines)
{
    if (!utf8 || !*utf8 || max_lines <= 0) {
        return 0;
    }
    int lines = 0;
    int cx = 0;
    const char *line_start = utf8;
    const char *cur = utf8;
    const char *next = utf8;
    const char *ws_end = NULL; /* 当前行内最近空格之后的位置 */
    bool run_ascii = true;     /* 空格后的连续片段是否纯 ASCII(整词) */

    while (*next) {
        cur = next;
        const char *p = cur;
        uint32_t cp = fb_utf8_next(&p);
        next = p;
        if (cp == '\n') {
            if (lines >= max_lines) {
                return lines;
            }
            emit_segment(f, x, y + lines * FB_LINE_H, max_w, line_start, cur,
                         bold, center);
            lines++;
            line_start = next;
            cx = 0;
            ws_end = NULL;
            run_ascii = true;
            continue;
        }
        int w = fb_glyph_width(cp);
        if (cx + w > max_w && cx > 0) {
            if (lines >= max_lines) {
                return lines;
            }
            /* 优先在空格处断开整词, 避免 ASCII 单词被拦腰截断 */
            const char *brk = cur;
            if (ws_end && ws_end > line_start && run_ascii) {
                brk = ws_end;
            }
            emit_segment(f, x, y + lines * FB_LINE_H, max_w, line_start, brk,
                         bold, center);
            lines++;
            line_start = brk;
            cx = seg_width(line_start, next);
            ws_end = (cp == ' ') ? next : NULL;
            run_ascii = cp < 0x80;
            if (cx > max_w) {
                cx = max_w; /* 超长词: 后续按字符硬折 */
            }
            continue;
        }
        cx += w;
        if (cp == ' ') {
            ws_end = next;
            run_ascii = true;
        } else if (cp >= 0x80) {
            run_ascii = false;
        }
    }
    if (line_start < next && lines < max_lines) {
        emit_segment(f, x, y + lines * FB_LINE_H, max_w, line_start, next,
                     bold, center);
        lines++;
    }
    return lines;
}

int fb_draw_centered(fb_t *f, int y, const char *utf8, bool bold)
{
    int w = fb_text_width(utf8);
    int max_w = FB_W - 8;
    int x;
    if (w > max_w) {
        x = 4;
    } else {
        x = (FB_W - w) / 2;
    }
    fb_draw_line(f, x, y, utf8, bold);
    return w;
}

void fb_hline(fb_t *f, int x, int y, int w)
{
    for (int i = 0; i < w; i++) {
        fb_pixel(f, x + i, y, true);
    }
}
