#ifndef ZG_WIFI_NET_H
#define ZG_WIFI_NET_H

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * 连接 STA。内部完成 netif/event/wifi 初始化（可重复调用）。
 * 超时或重试(10 次)失败返回 ESP_FAIL。
 */
esp_err_t wifi_net_connect(const char *ssid, const char *pass, uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
#endif /* ZG_WIFI_NET_H */
