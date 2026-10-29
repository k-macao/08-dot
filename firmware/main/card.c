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

/* 幸运栏: 「幸运 色 数」+「贵人 星座」。
 * 一行放不下时显式换行 —— 交给折行器会出现「幸运…贵 / 人 天蝎座」这种把
 * 「贵人」拦腰截断的断行。返回行数。 */
static int build_lucky(const fortune_t *f, char *out, size_t cap)
{
    out[0] = 0;
    if (!f->color[0] && !f->number[0] && !f->buddy[0]) {
        return 0;
    }
    int off = xappend(out, cap, 0, "幸运");
    if (f->color[0]) {
        off = xappend(out, cap, off, " %s", f->color);
    }
    if (f->number[0]) {
        off = xappend(out, cap, off, " %s", f->number);
    }
    if (f->buddy[0]) {
        char tail[40];
        snprintf(tail, sizeof(tail), " · 贵人 %s", f->buddy);
        if (fb_text_width(out) + fb_text_width(tail) > CW) {
            xappend(out, cap, off, "\n贵人 %s", f->buddy);
        } else {
            xappend(out, cap, off, "%s", tail);
        }
    }
    return fb_wrap_count(out, CW);
}

/* 文案超过 max_lines 行时逐字截断并补省略号。
 * 旧实现先砍掉尾部再试加「…」, 会出现「截断处留着半个字/标点」或省略号
 * 撑出额外行的情况; 这里改成: 逐字符推进, 每步都试算「前缀 + …」的行数,
 * 只有放得下才保留; 同时在句读处优先收尾(符合 README 的排版约定)。 */
#define MORE_MARK "…"

static void clip_text(char *s, size_t cap, int max_w, int max_lines)
{
    if (!s || !s[0] || fb_wrap_count(s, max_w) <= max_lines) {
        return;
    }
    char prefix[sizeof(((fortune_t *)0)->overall)];
    prefix[0] = 0;
    size_t keep = 0;      /* 已确认可行、最长的前缀字节数 */
    size_t soft = 0;      /* 句读处收尾位置 */
    size_t n = strlen(s);
    const char *p = s;
    while ((size_t)(p - s) < n) {
        const char *cs = p;
        uint32_t cp = fb_utf8_next(&p);
        if (cp == 0) {
            break;
        }
        size_t len = (size_t)(p - cs);
        if (len + 4 >= sizeof(prefix)) {
            break;
        }
        memcpy(prefix, s, (size_t)(p - s));
        memcpy(prefix + (p - s), MORE_MARK, 4); /* … + NUL */
        if (fb_wrap_count(prefix, max_w) > max_lines) {
            break;
        }
        keep = (size_t)(p - s);
        if (cp == 0x3002 || cp == 0xFF01 || cp == 0xFF1F || cp == 0xFF1B ||
            cp == 0xFF0C || cp == 0x3001) {
            soft = keep;
        }
    }
    if (soft > 0 && soft <= keep) {
        keep = soft; /* 断在句读处更自然 */
    }
    while (keep > 0 && s[keep - 1] == ' ') {
        keep--;
    }
    /* 省略号前不必留逗号/顿号: 「沟通顺畅，…」→「沟通顺畅…」 */
    if (keep >= 3 && ((unsigned char)s[keep - 3] == 0xE3 &&
                      (unsigned char)s[keep - 2] == 0x80 &&
                      (unsigned char)s[keep - 1] == 0x81)) {
        keep -= 3; /* 、 */
    } else if (keep >= 3 && ((unsigned char)s[keep - 3] == 0xEF &&
                             (unsigned char)s[keep - 2] == 0xBC &&
                             (unsigned char)s[keep - 1] == 0x8C)) {
        keep -= 3; /* ， */
    }
    if (keep + 4 > cap) {
        return;
    }
    memcpy(s + keep, MORE_MARK, 4);
}

void card_show(fortune_t *f, const char *title, const char *date_line)
{
    ensure_epd();
    fb_clear(&s_fb);

    int y = 4;
    if (title && title[0]) {
        fb_draw_centered(&s_fb, y, title, true);
        y += 20;
        fb_hline(&s_fb, M, y, CW);
        y += 5;
    }

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
    int nlucky = build_lucky(f, lucky, sizeof(lucky));

    int below = nd + (yi_line[0] ? 1 : 0) + (ji_line[0] ? 1 : 0) + nlucky;
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
        y += fb_draw_wrapped(&s_fb, M, y, CW, lucky, false, true, 3) * FB_LINE_H;
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
    y += 10;

    /* 文案按 152px 屏宽(正文 144px)实测排好, 每行都 ≤ 144px 且不靠折行,
     * 避免出现「连接 USB 后打开串 / 口终端」这类拦腰断行。
     * 改字请用 firmware/tools/preview_card.py --widths 复核宽度。 */
    static const char *const lines[] = {
        "等待配置 WiFi",      /* 98 */
        "连接 USB 到电脑",    /* 117 */
        "打开串口终端",       /* 96 */
        "串口 115200",        /* 90 */
        "输入 wifi 名称 密码", /* 133 */
        "星座固定 狮子座",    /* 116 */
        "输入 help 看命令",    /* 119 */
    };
    for (size_t i = 0; i < sizeof(lines) / sizeof(lines[0]); i++) {
        fb_draw_centered(&s_fb, y, lines[i], false);
        y += 20;
    }

    y += 6;
    fb_hline(&s_fb, M, y, CW);
    y += 20;
    fb_draw_centered(&s_fb, y, "每日 07:00 自动更新", false);

    flush();
    ESP_LOGW(TAG, "已进入配网模式 (串口命令行等待中)");
}
