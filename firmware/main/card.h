#ifndef ZG_CARD_H
#define ZG_CARD_H

#include "fortune.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 排版并上屏一张运势卡片, 随后进入墨水屏睡眠。
 * 布局: 标题(加粗) / 分隔线 / 星座·期数 / 星级·指数 /
 *       综合运势(自动截断) / 四维指数 / 宜忌 / 幸运 / 日期
 */
void card_show(fortune_t *f, const char *title, const char *date_line);

/** 首次启动的配网帮助卡片 */
void card_show_setup(const char *title);

#ifdef __cplusplus
}
#endif
#endif /* ZG_CARD_H */
