/**
 * @file wifi_net.c
 * @brief STA 联网（事件驱动 + 超时 + 断线重连）
 */
#include "wifi_net.h"

#include <string.h>

#include "esp_event.h"
#include "esp_netif_ip_addr.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

static const char *TAG = "wifi_net";

#define BIT_OK   BIT0
#define BIT_FAIL BIT1

#define WIFI_RETRIES_MAX 10

static EventGroupHandle_t s_evt;
static bool s_started;
static int s_retries;

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id,
                               void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retries < WIFI_RETRIES_MAX) {
            s_retries++;
            ESP_LOGW(TAG, "断开, 重连 %d/%d", s_retries, WIFI_RETRIES_MAX);
            esp_wifi_connect();
        } else if (s_evt) {
            xEventGroupSetBits(s_evt, BIT_FAIL);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "已联网 " IPSTR, IP2STR(&ev->ip_info.ip));
        s_retries = WIFI_RETRIES_MAX; /* 后续断开不再无限重连, 等待流程结束 */
        if (s_evt) {
            xEventGroupSetBits(s_evt, BIT_OK);
        }
    }
}

esp_err_t wifi_net_connect(const char *ssid, const char *pass,
                           uint32_t timeout_ms)
{
    if (!ssid || ssid[0] == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_evt) {
        s_evt = xEventGroupCreate();
        ESP_ERROR_CHECK(esp_netif_init());
        ESP_ERROR_CHECK(esp_event_loop_create_default());
        esp_netif_create_default_wifi_sta();
        ESP_ERROR_CHECK(esp_event_handler_instance_register(
            WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
        ESP_ERROR_CHECK(esp_event_handler_instance_register(
            IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));
        wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
        ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    }

    wifi_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    strncpy((char *)cfg.sta.ssid, ssid, sizeof(cfg.sta.ssid) - 1);
    if (pass) {
        strncpy((char *)cfg.sta.password, pass, sizeof(cfg.sta.password) - 1);
    }
    cfg.sta.threshold.authmode = WIFI_AUTH_OPEN; /* 开放/WPA2 都接受 */
    cfg.sta.pmf_cfg.capable = true;
    cfg.sta.pmf_cfg.required = false;

    s_retries = 0;
    xEventGroupClearBits(s_evt, BIT_OK | BIT_FAIL);

    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    if (!s_started) {
        ESP_ERROR_CHECK(esp_wifi_start());
        s_started = true;
    } else {
        esp_wifi_disconnect();
        esp_wifi_connect();
    }

    EventBits_t bits = xEventGroupWaitBits(s_evt, BIT_OK | BIT_FAIL, pdFALSE,
                                           pdFALSE, pdMS_TO_TICKS(timeout_ms));
    if (bits & BIT_OK) {
        return ESP_OK;
    }
    ESP_LOGE(TAG, "联网失败 (timeout=%ums)", (unsigned)timeout_ms);
    return ESP_FAIL;
}
