/**
 * @file app_cfg.c
 * @brief NVS 配置读写（WiFi / 固定狮子座 / 唤醒计数）
 */
#include "app_cfg.h"
#include "fortune.h"

#include <stdbool.h>
#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "app_cfg";
static const char *NS = "zhangyu";

/**
 * 读一个字符串键。
 * nvs_get_str() 的 length 是「入参=缓冲区大小, 出参=实际长度」；旧代码复用
 * 同一个未初始化的 size_t, 首次调用即把随机值当容量传入 —— 可能写穿缓冲区,
 * 也可能让后续键全部读失败。这里每次调用前重置为数组容量。
 */
static void load_str(nvs_handle_t h, const char *key, char *dst, size_t cap)
{
    if (cap == 0) {
        return;
    }
    size_t len = cap;
    if (nvs_get_str(h, key, dst, &len) != ESP_OK) {
        dst[0] = 0;
    }
    dst[cap - 1] = 0;
}

esp_err_t app_cfg_load(app_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err == ESP_OK) {
        load_str(h, "ssid", cfg->ssid, sizeof(cfg->ssid));
        load_str(h, "pass", cfg->pass, sizeof(cfg->pass));
        load_str(h, "smode", cfg->sign_mode, sizeof(cfg->sign_mode));
        load_str(h, "sign", cfg->sign, sizeof(cfg->sign));
        nvs_close(h);
    }

    /* menuconfig 默认值播种（CI 构建时可直接烧入 WiFi） */
    bool seed_wifi = (cfg->ssid[0] == 0 && CONFIG_ZHANGYU_WIFI_SSID[0] != 0);
    if (seed_wifi) {
        snprintf(cfg->ssid, sizeof(cfg->ssid), "%s", CONFIG_ZHANGYU_WIFI_SSID);
        snprintf(cfg->pass, sizeof(cfg->pass), "%s", CONFIG_ZHANGYU_WIFI_PASSWORD);
        ESP_LOGI(TAG, "已从 menuconfig 播种 WiFi 配置");
    }

    /* 每次启动都执行显示策略，而不只是在 NVS 为空时播种：
     * 旧固件保存的 random/其他星座不能覆盖「只显示狮子座」。
     * 只改星座键，保留已配好的 WiFi；迁移后写回，避免每次启动重复写。 */
    bool migrate_sign = strcmp(cfg->sign_mode, "fixed") != 0 ||
                        strcmp(cfg->sign, DISPLAY_SIGN_KEY) != 0;
    snprintf(cfg->sign_mode, sizeof(cfg->sign_mode), "fixed");
    snprintf(cfg->sign, sizeof(cfg->sign), "%s", DISPLAY_SIGN_KEY);
    if (seed_wifi || migrate_sign) {
        esp_err_t save_err = app_cfg_save(cfg);
        if (save_err != ESP_OK) {
            ESP_LOGW(TAG, "配置保存失败: %s（本次仍使用狮子座）",
                     esp_err_to_name(save_err));
        }
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
