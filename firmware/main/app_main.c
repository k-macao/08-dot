/**
 * @file app_main.c
 * @brief 章鱼AI.每日占一占 — 固件主流程
 *
 * 启动 → (未配置则进串口配网) → 联网 → SNTP 校时 → 拉取每日运势
 *      → 墨水屏渲染卡片 → 深度休眠至下一个唤醒时刻 (默认 07:00)
 *
 * 安全约定 (见 dot_open_platform 自定义固件指南):
 *   - 每次深度休眠前必定设置唤醒定时器, 绝不“无唤醒源休眠”
 */
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "app_cfg.h"
#include "card.h"
#include "cli.h"
#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_sleep.h"
#include "fortune.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "wifi_net.h"

static const char *TAG = "zhangyu";

#define TIME_VALID_TS 1600000000 /* 2020-07 之后视为已校时 */

static void try_sync_time(void)
{
    esp_sntp_config_t sntp_cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("ntp.aliyun.com");
    esp_err_t err = esp_netif_sntp_init(&sntp_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "SNTP init 失败: %s", esp_err_to_name(err));
        return;
    }
    err = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(15000));
    ESP_LOGI(TAG, "SNTP 同步: %s", esp_err_to_name(err));
    esp_netif_sntp_deinit();
}

static void date_line_build(char *out, size_t cap, int wake_no)
{
    time_t now = time(NULL);
    struct tm t;
    localtime_r(&now, &t);
    int year = t.tm_year + 1900;
    if (now > TIME_VALID_TS && year >= 2024 && year <= 2100) {
        /* 星期必须单独取一个字: 旧写法 kWeek + 3*wday 会把「周一」变成整串
         * 「周一三四五六」(C 字符串没有长度截断) */
        static const char *const kWeek[7] = {"日", "一", "二", "三",
                                             "四", "五", "六"};
        snprintf(out, cap, "%d月%d日 周%s", t.tm_mon + 1, t.tm_mday,
                 kWeek[t.tm_wday >= 0 && t.tm_wday < 7 ? t.tm_wday : 0]);
    } else {
        snprintf(out, cap, "章鱼AI 第%d占", wake_no);
    }
}

/**
 * 深度休眠。
 *   success=true  → 睡到下一个配置时刻 (Kconfig 默认 07:00)
 *   success=false → 12 小时后重试 (网络故障时不空等一整天)
 */
static void go_sleep(bool success)
{
    uint64_t us = 12ULL * 3600ULL * 1000000ULL;
    time_t now = time(NULL);
    if (success && now > TIME_VALID_TS) {
        struct tm t;
        localtime_r(&now, &t);
        struct tm target = t;
        target.tm_hour = CONFIG_ZHANGYU_WAKE_HOUR;
        target.tm_min = CONFIG_ZHANGYU_WAKE_MINUTE;
        target.tm_sec = 0;
        time_t ts = mktime(&target);
        if (ts <= now + 600) {
            ts += 86400; /* 今天的目标点已过 → 明天 */
        }
        if (ts > now + 600) {
            us = (uint64_t)(ts - now) * 1000000ULL;
        }
    }
    ESP_LOGI(TAG, "深度休眠 %.2f 小时 (success=%d)", (double)us / 3.6e9,
             (int)success);
    ESP_ERROR_CHECK(esp_sleep_enable_timer_wakeup(us));
    esp_deep_sleep_start();
}

static void run_daily(app_cfg_t *cfg)
{
    int wake = app_cfg_bump_wake();

    bool net = (wifi_net_connect(cfg->ssid, cfg->pass, 20000) == ESP_OK);
    time_t now = time(NULL);
    if (net && now < TIME_VALID_TS) {
        try_sync_time(); /* 首次开机/掉电后校时 */
        now = time(NULL);
    }

    char date_line[48];
    date_line_build(date_line, sizeof(date_line), wake);

    /* 决定本次占卜的星座: fixed 指定 / random 每日抽签(同日幂等) */
    int idx = -1;
    if (strcmp(cfg->sign_mode, "fixed") == 0) {
        idx = fortune_sign_index(cfg->sign);
    }
    if (idx < 0) {
        if (now > TIME_VALID_TS) {
            struct tm t;
            localtime_r(&now, &t);
            uint32_t seed = (uint32_t)((t.tm_year + 1900) * 10000 +
                                       (t.tm_mon + 1) * 100 + t.tm_mday);
            seed ^= 0x5F3759DFu;
            idx = (int)((seed * 2654435761u) >> 8) % SIGN_COUNT;
        } else {
            idx = wake % SIGN_COUNT;
        }
    }
    const char *key = fortune_sign_key(idx);
    ESP_LOGI(TAG, "本次占卜: %s (%s) | 网络=%d | %s", fortune_sign_cn(idx), key,
             (int)net, date_line);

    fortune_t f;
    fortune_get(key, net, &f);

    card_show(&f, CONFIG_ZHANGYU_TITLE, date_line);
    go_sleep(net);
}

void app_main(void)
{
    setenv("TZ", "CST-8", 1); /* 京/澳时区 (UTC+8) */
    tzset();

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    app_cfg_t cfg;
    app_cfg_load(&cfg);

    if (cfg.ssid[0] == 0) {
        ESP_LOGW(TAG, "未配置 WiFi → 进入串口配网模式");
        card_show_setup(CONFIG_ZHANGYU_TITLE);
        cli_run(&cfg); /* 阻塞, 直到 wifi/go */
    }

    run_daily(&cfg); /* 不返回: 以深度休眠结束 */
}
