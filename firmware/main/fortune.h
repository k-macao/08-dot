#ifndef ZG_FORTUNE_H
#define ZG_FORTUNE_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SIGN_COUNT 12
#define DISPLAY_SIGN_KEY "leo"
#define DISPLAY_SIGN_INDEX 4

typedef struct {
    char sign_key[12];  /* aries.. */
    char sign_cn[12];   /* 白羊座.. (UTF-8 需 10B) */
    char period[16];    /* 今日运势 */
    char comment[32];   /* 简评 */
    int score;          /* 综合 1..5, 0=未知 */
    char idx[8];        /* 综合指数 "73%" */
    char love[8], work[8], money[8], health[8];
    char overall[256];  /* 综合运势正文 (UTF-8) */
    char yi[36], ji[36];
    char color[20], number[8], buddy[16];
    char tag[16];       /* footer 数据源短标: xxapi / en / 离线·降级 */
} fortune_t;

/** 按英文 key 或中文名找星座下标; 失败返回 -1 */
int fortune_sign_index(const char *name);
const char *fortune_sign_key(int idx);
const char *fortune_sign_cn(int idx);

/**
 * 数据源优先级获取: xxapi → freehoroscope → 离线兜底。
 * 任一失败自动降级; network_ok 为假时直接离线。
 * 返回最终使用的数据源 tag (写入 out->tag)。
 */
void fortune_get(const char *sign_key, bool network_ok, fortune_t *out);

/** 离线占卜引擎 (确定性: 同日期同星座结果恒定) */
void fortune_offline(const char *sign_key, int y, int m, int d, int salt,
                     const char *tag, fortune_t *out);

#ifdef __cplusplus
}
#endif
#endif /* ZG_FORTUNE_H */
