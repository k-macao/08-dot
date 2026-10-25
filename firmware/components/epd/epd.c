/**
 * @file epd.c
 * @brief Quote/0 墨水屏统一接口实现
 * @license Apache-2.0 (wrapper); 驱动本体来自 dot_open_platform
 */
#include "epd.h"

#include "driver/spi_master.h"
#include "sdkconfig.h"

#if CONFIG_ZHANGYU_EPD_UC8151
#include "uc8151_minimal.h"
#else
#include "uc8251d_minimal.h"
#endif

/* Quote/0 默认显示配置（见 dot_open_platform 自定义固件指南 / pinout.md）
 *   VIN=20 BUSY=3 RESET=4 DC=5 CS=6 SCK=10 MOSI=7, SPI2_HOST @15MHz */
#define EPD_SPI_CLOCK_HZ (15 * 1000 * 1000)

#if CONFIG_ZHANGYU_EPD_UC8151
static const uc8151_config_t s_cfg = {
    .spi_host = SPI2_HOST,
    .pin_vin = 20,
    .pin_busy = 3,
    .pin_reset = 4,
    .pin_dc = 5,
    .pin_cs = 6,
    .pin_sck = 10,
    .pin_mosi = 7,
    .spi_clock_hz = EPD_SPI_CLOCK_HZ,
};
#else
static const uc8251d_config_t s_cfg = {
    .spi_host = SPI2_HOST,
    .pin_vin = 20,
    .pin_busy = 3,
    .pin_reset = 4,
    .pin_dc = 5,
    .pin_cs = 6,
    .pin_sck = 10,
    .pin_mosi = 7,
    .spi_clock_hz = EPD_SPI_CLOCK_HZ,
};
#endif

esp_err_t epd_init(void)
{
#if CONFIG_ZHANGYU_EPD_UC8151
    return uc8151_init(&s_cfg);
#else
    return uc8251d_init(&s_cfg);
#endif
}

esp_err_t epd_show(const uint8_t *data)
{
#if CONFIG_ZHANGYU_EPD_UC8151
    return uc8151_display(data);
#else
    return uc8251d_display(data);
#endif
}

esp_err_t epd_clear_white(void)
{
#if CONFIG_ZHANGYU_EPD_UC8151
    return uc8151_clear(true);
#else
    return uc8251d_clear(true);
#endif
}

esp_err_t epd_sleep(void)
{
#if CONFIG_ZHANGYU_EPD_UC8151
    return uc8151_sleep();
#else
    return uc8251d_sleep();
#endif
}
