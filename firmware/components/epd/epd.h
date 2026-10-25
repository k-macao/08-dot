/**
 * @file epd.h
 * @brief Quote/0 墨水屏统一接口（按 Kconfig 选择标准版/首发版驱动）
 *
 * 驱动源码来自 MindReset/dot_open_platform (Apache-2.0)
 * 引脚映射来自 devices/quote_0/firmware-resources/gpio/pinout.md
 */
#ifndef ZG_EPD_H
#define ZG_EPD_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define EPD_WIDTH        152
#define EPD_HEIGHT       296
#define EPD_BUFFER_SIZE  ((EPD_WIDTH * EPD_HEIGHT) / 8)  /* 5624 */

/** 初始化 SPI 与面板（内部按 Kconfig 选 UC8251D / UC8151） */
esp_err_t epd_init(void);

/** 全刷一帧；data 每 bit 一位像素，1=白 0=黑，长度 EPD_BUFFER_SIZE */
esp_err_t epd_show(const uint8_t *data);

/** 清屏（白） */
esp_err_t epd_clear_white(void);

/** 睡眠并释放显示电源 (GPIO20) */
esp_err_t epd_sleep(void);

#ifdef __cplusplus
}
#endif
#endif /* ZG_EPD_H */
