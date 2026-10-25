/**
 * @file card.c
 * @brief 卡片排版与上屏 (152x296)
 */
#include "card.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "epd.h"
#include "esp_log.h"
#include "fb.h"

static const char *TAG = "card";

#define M            4
#define CW           (FB_W - 2 * M) /* 144 */
#define BODY_END     250            /* 正文行顶上限 */
#define RULE_FOOT_Y  266
#define FOOT_Y       270

static fb_t s_fb;
static bool s_ready;

static void ensure_epd(void)
{
    if (!s_ready) {
        esp_err_t err = epd_init();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "epd_init 失败: %s", esp_err_to_name(err));
        }
        s_ready = true;
    }
}

static void flush(void)
{
    esp_err_t err = epd_show(s_fb.buf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "epd_show 失败: %s", esp_err_to_name(err));
    }
    epd_sleep();
}

/* 可裁剪的追加格式化 */
static int xappend(char *buf, size_t cap, int off, const char *fmt, ...)
{
    if ((size_t)off >= cap - 1) {
        return (int)cap - 1;
    }
    va_list ap;
    va_start(ap, fmt);
    int r = vsnprintf(buf + off, cap - off, fmt, ap);
    va_end(ap);
    if (r < 0) {
        return off;
    }
    off += r;
    if ((size_t)off >= cap) {
        off = (int)cap - 1;
    }
    return off;
}

static void make_stars(int score, char *out, size_t cap)
{
    out[0] = 0;
    if (score < 1 || score > 5 || cap < 16) {
        return;
    }
    size_t o = 0;
    for (int i = 0; i < 5 && o + 4 < cap; i++) {
        const char *s = (i < score) ? "★" : "☆";
        memcpy(out + o, s, 3);
        o += 3;
    }
    out[o] = 0;
}

static void todo_line(char *out, size_t cap, const char *word, const char *raw)
{
    out[0] = 0;
    if (!raw || !raw[0]) {
        return;
    }
    size_t wl = strlen(word);
    const char *p = raw;
    if (strncmp(p, word, wl) == 0) {
        p += wl;
        while (*p == ' ') {
            p++;
        }
    }
    if (!*p) {
        return;
    }
    snprintf(out, cap, "%s %s", word, p);
}

static int build_dims(const fortune_t *f, char lines[][64])
{
    static const char *labels[4] = {"爱情", "事业", "财运", "健康"};
    const char *vals[4] = {f->love, f->work, f->money, f->health};
    char parts[4][24];
    int n = 0;
    for (int i = 0; i < 4; i++) {
        if (vals[i][0] && n < 4) {
            snprintf(parts[n], sizeof(parts[0]), "%s%s", labels[i], vals[i]);
            n++;
        }
    }
    if (n == 0) {
        return 0;
    }
    if (n == 1) {
        snprintf(lines[0], 64, "%s", parts[0]);
        return 1;
    }
    snprintf(lines[0], 64, "%s %s", parts[0], parts[1]);
    if (n == 2) {
        return 1; /* 两个合一行 */
    }
    if (n == 3) {
        snprintf(lines[1], 64, "%s", parts[2]);
        return 2;
    }
    snprintf(lines[1], 64, "%s %s", parts[2], parts[3]);
    return 2;
}

static void build_lucky(const fortune_t *f, char *out, size_t cap)
{
    out[0] = 0;
    if (!f->color[0] && !f->number[0] && !f->buddy[0]) {
        return;
    }
    int off = xappend(out, cap, 0, "幸运");
    if (f->color[0]) {
        off = xappend(out, cap, off, " %s", f->color);
    }
    if (f->number[0]) {
        off = xappend(out, cap, off, " %s", f->number);
    }
    if (f->buddy[0]) {
        off = xappend(out, cap, off, " · 贵人 %s", f->buddy);
    }
    (void)off;
}

/* 文案超过 max_lines 行时按字符截断并补省略号 */
static void clip_text(char *s, size_t cap, int max_w, int max_lines)
{
    if (fb_wrap_count(s, max_w) <= max_lines) {
        return;
    }
    size_t n = strlen(s);
    while (n > 6) {
        /* 回退一个 UTF-8 字符 */
        n--;
        while (n > 0 && ((unsigned char)s[n] & 0xC0) == 0x80) {
            n--;
        }
        s[n] = 0;
        if (fb_wrap_count(s, max_w) <= max_lines) {
            break;
        }
    }
    /* 追加省略号: 若会撑出额外行则放弃 */
    n = strlen(s);
    if (n + 4 <= cap) {
        memcpy(s + n, "…", 4); /* … + NUL */
        if (fb_wrap_count(s, max_w) > max_lines) {
            s[n] = 0;
        }
    }
}

void card_show(fortune_t *f, const char *title, const char *date_line)
{
    ensure_epd();
    fb_clear(&s_fb);

    int y = 4;
    fb_draw_centered(&s_fb, y, title, true);
    y += 20;
    fb_hline(&s_fb, M, y, CW);
    y += 5;

    char line[160];
    snprintf(line, sizeof(line), "%s · %s", f->sign_cn, f->period);
    fb_draw_centered(&s_fb, y, line, false);
    y += FB_LINE_H;

    /* 星级 + 综合指数 (无评分数据时退回简评) */
    char stars[16];
    make_stars(f->score, stars, sizeof(stars));
    if (stars[0] && f->idx[0]) {
        snprintf(line, sizeof(line), "%s %s", stars, f->idx);
        fb_draw_centered(&s_fb, y, line, false);
        y += FB_LINE_H;
    } else if (f->idx[0]) {
        fb_draw_centered(&s_fb, y, f->idx, false);
        y += FB_LINE_H;
    } else if (f->comment[0]) {
        snprintf(line, sizeof(line), "「%s」", f->comment);
        fb_draw_centered(&s_fb, y, line, false);
        y += FB_LINE_H;
    }

    /* 下方固定字段预留 */
    char dims[2][64] = {{0}};
    int nd = build_dims(f, dims);
    char yi_line[48], ji_line[48], lucky[112];
    todo_line(yi_line, sizeof(yi_line), "宜", f->yi);
    todo_line(ji_line, sizeof(ji_line), "忌", f->ji);
    build_lucky(f, lucky, sizeof(lucky));

    int below = nd + (yi_line[0] ? 1 : 0) + (ji_line[0] ? 1 : 0) +
                fb_wrap_count(lucky, CW);
    int reserved = below * FB_LINE_H + (below ? 4 : 0);
    int avail = BODY_END - y - reserved;
    int max_text = f->overall[0] ? avail / FB_LINE_H : 0;
    if (f->overall[0] && max_text < 1) {
        max_text = 1;
    }
    if (max_text > 0) {
        clip_text(f->overall, sizeof(f->overall), CW, max_text);
    }
    y += fb_draw_wrapped(&s_fb, M, y, CW, f->overall, false, false, max_text) *
         FB_LINE_H;

    for (int i = 0; i < nd && y <= BODY_END; i++) {
        fb_draw_centered(&s_fb, y, dims[i], false);
        y += FB_LINE_H;
    }
    if (yi_line[0] && y <= BODY_END) {
        fb_draw_centered(&s_fb, y, yi_line, false);
        y += FB_LINE_H;
    }
    if (ji_line[0] && y <= BODY_END) {
        fb_draw_centered(&s_fb, y, ji_line, false);
        y += FB_LINE_H;
    }
    if (lucky[0]) {
        y += fb_draw_wrapped(&s_fb, M, y, CW, lucky, false, true, 2) * FB_LINE_H;
    }
    (void)y;

    fb_hline(&s_fb, M, RULE_FOOT_Y, CW);
    if (date_line && date_line[0]) {
        fb_draw_centered(&s_fb, FOOT_Y, date_line, false);
    }

    flush();
    ESP_LOGI(TAG, "已上屏: %s %s | %.40s", f->sign_cn, f->tag, f->overall);
}

void card_show_setup(const char *title)
{
    ensure_epd();
    fb_clear(&s_fb);

    int y = 8;
    fb_draw_centered(&s_fb, y, title, true);
    y += 22;
    fb_hline(&s_fb, M, y, CW);
    y += 8;

    static const char *block =
        "等待配置 WiFi\n"
        "连接 USB 后打开串口终端\n"
        "wifi <SSID> [密码]\n"
        "sign 狮子座 或 sign random\n"
        "输入 help 查看全部命令";
    y += fb_draw_wrapped(&s_fb, M, y, CW, block, false, true, 9) * FB_LINE_H;
    (void)y;

    flush();
    ESP_LOGW(TAG, "已进入配网模式 (串口命令行等待中)");
}
