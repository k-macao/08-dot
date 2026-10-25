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

/* ------------------------------------------------------------------ */
/* 折行内核 (fb_wrap_count 与 fb_draw_wrapped 共用同一套断点规则)        */
/*                                                                     */
/* 规则:                                                               */
/*   - \n 强制换行;                                                    */
/*   - 中日韩字符之间任意位置可断, ASCII 单词只在其后的空格处断,        */
/*     避免 "USB" 被拆成 "US" / "B";                                   */
/*   - 避头点: ，。、）」』 等不能落在行首 (断点前移 1 字, 绝不超宽);     */
/*   - 避尾点: 「（《【 等不能落在行尾 (推到下一行)。                    */
/* ------------------------------------------------------------------ */

typedef struct {
    const char *end;   /* 本行结束位置 (已去掉行尾空格) */
    const char *next;  /* 下一行起点 (已跳过空格与换行) */
    int width;         /* 本行像素宽 */
} line_seg_t;

/* 不能出现在行首的字符 (避头点) */
static bool no_line_start(uint32_t cp)
{
    switch (cp) {
    case 0x3001: /* 、 */ case 0x3002: /* 。 */ case 0xFF0C: /* ， */
    case 0xFF1B: /* ； */ case 0xFF1A: /* ： */ case 0xFF1F: /* ？ */
    case 0xFF01: /* ！ */ case 0xFF09: /* ） */ case 0x3011: /* 】 */
    case 0x300B: /* 》 */ case 0x300D: /* 」 */ case 0x300F: /* 』 */
    case 0x3015: /* 〕 */ case 0x3017: /* 〕 */ case 0xFF3D: /* ］ */
    case 0xFF5D: /* ｝ */ case 0x201D: /* ” */ case 0x2019: /* ’ */
    case 0x2026: /* … */ case 0x00B7: /* · */ case 0x2030: /* ‰ */
    case 0x2103: /* ℃ */ case 0xFF05: /* ％ */
    case ',': case '.': case ';': case ':': case '!': case '?':
    case ')': case ']': case '}': case '%':
        return true;
    default:
        return false;
    }
}

/* 不能出现在行尾的字符 (避尾点) */
static bool no_line_end(uint32_t cp)
{
    switch (cp) {
    case 0xFF08: /* （ */ case 0x3010: /* 【 */ case 0x300A: /* 《 */
    case 0x300C: /* 「 */ case 0x300E: /* 『 */ case 0x3014: /* 〔 */
    case 0x3016: /* 〔 */ case 0xFF3B: /* ［ */ case 0xFF5B: /* ｛ */
    case 0x201C: /* “ */ case 0x2018: /* ‘ */
    case '(': case '[': case '{':
        return true;
    default:
        return false;
    }
}

/* 宽字符 (CJK 等): 可在任意字符间断行 */
static bool cp_is_wide(uint32_t cp)
{
    return cp >= 0x1100;
}

/* 计算 [s,e) 段像素宽 */
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

/* 取以 pos 结尾的上一字符的起始位置 */
static const char *prev_char(const char *s, const char *pos)
{
    const char *p = s;
    const char *last = s;
    while (p < pos) {
        last = p;
        uint32_t cp = fb_utf8_next(&p);
        if (cp == 0) {
            break;
        }
    }
    return (last < pos) ? last : s;
}

/* 从 s 切出一行。返回 false 表示已到结尾 */
static bool line_split(const char *s, int max_w, line_seg_t *seg)
{
    if (!s || !*s) {
        return false;
    }
    if (max_w < 1) {
        max_w = 1;
    }
    if (*s == '\n') { /* 空行 */
        seg->end = s;
        seg->next = s + 1;
        seg->width = 0;
        return true;
    }

    const char *p = s;
    const char *hard = NULL;  /* 放不下时当前字符的起点 (硬断) */
    const char *soft = NULL;  /* 最近的合法断点 (软断) */
    int soft_w = 0;
    int width = 0;

    while (*p) {
        const char *cs = p;
        uint32_t cp = fb_utf8_next(&p);
        if (cp == '\n') {
            seg->end = cs;
            seg->next = p;
            seg->width = width;
            return true;
        }
        int gw = fb_glyph_width(cp);
        if (width + gw > max_w && width > 0) {
            hard = cs;
            break;
        }
        width += gw;
        if (cp == ' ' || cp_is_wide(cp)) {
            soft = p; /* 断在空格/宽字符之后 */
            soft_w = width;
        }
    }

    /* 整段放得下 */
    if (!*p && !hard) {
        seg->end = p;
        seg->next = p;
        seg->width = width;
        return true;
    }

    const char *end = (soft && soft > s) ? soft : hard;
    if (!end) {
        end = p;
    }
    int end_w = (soft && soft > s) ? soft_w : width;

    /* 避头点: 断点后的标点是标点 → 断点前移一个字, 让标点跟上一字同行。
     * 不能用「把标点回拉到本行」的做法: 那会超出 max_w, 画出右边距外被裁掉。 */
    for (int guard = 0; guard < 3 && end > s; guard++) {
        const char *q = end;
        uint32_t cp = fb_utf8_next(&q);
        if (cp == 0 || cp == '\n' || !no_line_start(cp)) {
            break;
        }
        const char *prev = prev_char(s, end);
        if (prev <= s) {
            break; /* 行内只剩一个字, 让它带标点一起超一点点 */
        }
        end = prev;
        end_w = seg_width(s, end);
    }
    /* 避尾点: 行末的开启标点推到下一行 */
    while (end > s) {
        const char *pc = prev_char(s, end);
        const char *q = pc;
        uint32_t cp = fb_utf8_next(&q);
        if (!no_line_end(cp)) {
            break;
        }
        end_w -= fb_glyph_width(cp);
        end = pc;
    }
    /* 去掉行尾空格 */
    while (end > s && end[-1] == ' ') {
        end--;
    }
    if (end <= s) { /* 兜底: 至少推进一个字符, 防止死循环 */
        end = s;
        fb_utf8_next(&end);
        end_w = fb_glyph_width((uint32_t)(unsigned char)*s);
    }

    const char *next = end;
    while (*next == ' ') {
        next++;
    }
    if (*next == '\n') {
        next++;
    }
    if (next <= s) {
        next = end + 1;
    }

    seg->end = end;
    seg->next = next;
    seg->width = end_w;
    return true;
}

int fb_wrap_count(const char *utf8, int max_w)
{
    if (!utf8 || !*utf8) {
        return 0;
    }
    int lines = 0;
    const char *p = utf8;
    line_seg_t seg;
    while (*p && lines < 256) {
        if (!line_split(p, max_w, &seg)) {
            break;
        }
        lines++;
        p = seg.next;
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

/* 绘制 [s,e) 段; center 为真时在 [x, x+max_w) 内居中 */
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
    const char *p = s;
    while (p < e) {
        uint32_t cp = fb_utf8_next(&p);
        if (cp == 0) {
            break;
        }
        lx += draw_one(f, lx, y, cp, bold);
    }
}

int fb_draw_wrapped(fb_t *f, int x, int y, int max_w, const char *utf8,
                    bool bold, bool center, int max_lines)
{
    if (!utf8 || !*utf8 || max_lines <= 0) {
        return 0;
    }
    int lines = 0;
    const char *p = utf8;
    line_seg_t seg;
    while (*p && lines < max_lines) {
        if (!line_split(p, max_w, &seg)) {
            break;
        }
        emit_segment(f, x, y + lines * FB_LINE_H, max_w, p, seg.end, bold,
                     center);
        lines++;
        p = seg.next;
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
