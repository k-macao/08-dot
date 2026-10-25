/**
 * @file app_cfg.c
 * @brief NVS 配置读写（WiFi / 抽签模式 / 唤醒计数）
 */
#include "app_cfg.h"

#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "app_cfg";
static const char *NS = "zhangyu";

esp_err_t app_cfg_load(app_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err == ESP_OK) {
        size_t n;
        nvs_get_str(h, "ssid", cfg->ssid, &n);
        nvs_get_str(h, "pass", cfg->pass, &n);
        nvs_get_str(h, "smode", cfg->sign_mode, &n);
        nvs_get_str(h, "sign", cfg->sign, &n);
        nvs_close(h);
    }

    /* menuconfig 默认值播种（CI 构建时可直接烧入 WiFi） */
    if (cfg->ssid[0] == 0 && CONFIG_ZHANGYU_WIFI_SSID[0] != 0) {
        strncpy(cfg->ssid, CONFIG_ZHANGYU_WIFI_SSID, sizeof(cfg->ssid) - 1);
        strncpy(cfg->pass, CONFIG_ZHANGYU_WIFI_PASSWORD, sizeof(cfg->pass) - 1);
        app_cfg_save(cfg);
        ESP_LOGI(TAG, "已从 menuconfig 播种 WiFi 配置");
    }
    if (cfg->sign_mode[0] == 0) {
        strncpy(cfg->sign_mode, CONFIG_ZHANGYU_SIGN_MODE, sizeof(cfg->sign_mode) - 1);
        strncpy(cfg->sign, CONFIG_ZHANGYU_SIGN, sizeof(cfg->sign) - 1);
    }
    ESP_LOGI(TAG, "配置: ssid=%s mode=%s sign=%s",
             cfg->ssid[0] ? cfg->ssid : "(未设置)", cfg->sign_mode, cfg->sign);
    return ESP_OK;
}

esp_err_t app_cfg_save(const app_cfg_t *cfg)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    nvs_set_str(h, "ssid", cfg->ssid);
    nvs_set_str(h, "pass", cfg->pass);
    nvs_set_str(h, "smode", cfg->sign_mode);
    nvs_set_str(h, "sign", cfg->sign);
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}

esp_err_t app_cfg_forget(app_cfg_t *cfg)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    nvs_erase_key(h, "ssid");
    nvs_erase_key(h, "pass");
    err = nvs_commit(h);
    nvs_close(h);
    memset(cfg->ssid, 0, sizeof(cfg->ssid));
    memset(cfg->pass, 0, sizeof(cfg->pass));
    return err;
}

int app_cfg_bump_wake(void)
{
    nvs_handle_t h;
    uint32_t v = 0;
    if (nvs_open(NS, NVS_READWRITE, &h) != ESP_OK) {
        return 1;
    }
    nvs_get_u32(h, "wake", &v);
    v = (v + 1) % 1000000u;
    nvs_set_u32(h, "wake", v);
    nvs_commit(h);
    nvs_close(h);
    return (int)v;
}
