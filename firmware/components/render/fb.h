/**
 * @file fb.h
 * @brief 152x296 单色帧缓冲 + UTF-8 文本渲染（16px 像素字库）
 *        像素约定与驱动一致: 1=白 0=黑
 */
#ifndef ZG_FB_H
#define ZG_FB_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FB_W        152
#define FB_H        296
#define FB_STRIDE   (FB_W / 8)          /* 19 */
#define FB_SIZE     ((FB_W * FB_H) / 8) /* 5624 */
#define FB_LINE_H   18                  /* 行高(16px 字 + 2px 行距) */

typedef struct {
    uint8_t buf[FB_SIZE];
} fb_t;

void fb_clear(fb_t *f); /* 全白 */
void fb_pixel(fb_t *f, int x, int y, bool black);

/** 取下一个 UTF-8 字符的码点，*p 自动前进；结尾返回 0 */
uint32_t fb_utf8_next(const char **p);

/** 字形横向步进宽度(px)。缺失字形返回占位宽度(7) */
int fb_glyph_width(uint32_t cp);

/** 单行文本自然宽度(px)，不含换行处理 */
int fb_text_width(const char *utf8);

/** 统计按 max_w 折行后的行数（遇 \n 强制换行） */
int fb_wrap_count(const char *utf8, int max_w);

/**
 * 绘制单行（不折行），y 为行顶。bold 为真时叠描 1px 加粗。
 * x 可为负。返回实际宽度。
 */
int fb_draw_line(fb_t *f, int x, int y, const char *utf8, bool bold);

/**
 * 折行绘制。max_lines 限制行数（超出部分丢弃）。
 * center 为真时每行居中（超宽行退化为从 x 左对齐）。
 * 返回实际绘制行数。
 */
int fb_draw_wrapped(fb_t *f, int x, int y, int max_w, const char *utf8,
                    bool bold, bool center, int max_lines);

/** 居中绘制单行；超宽时左对齐于 x。返回宽度。 */
int fb_draw_centered(fb_t *f, int y, const char *utf8, bool bold);

/** 水平线（1px 黑） */
void fb_hline(fb_t *f, int x, int y, int w);

#ifdef __cplusplus
}
#endif
#endif /* ZG_FB_H */
