#ifndef ZG_APP_CFG_H
#define ZG_APP_CFG_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_SSID_MAX 32
#define CFG_PASS_MAX 64

typedef struct {
    char ssid[CFG_SSID_MAX];
    char pass[CFG_PASS_MAX];
    char sign_mode[8]; /* 固定 "fixed"，兼容旧 NVS 布局 */
    char sign[12];     /* 固定 "leo"；旧抽签/其他星座设置在加载时迁移 */
} app_cfg_t;

/** 从 NVS 读取 WiFi；星座固定为狮子座，旧设置自动迁移，WiFi 保留 */
esp_err_t app_cfg_load(app_cfg_t *cfg);

/** 保存到 NVS */
esp_err_t app_cfg_save(const app_cfg_t *cfg);

/** 清除 WiFi 配置（回到配网模式）；星座设置保留 */
esp_err_t app_cfg_forget(app_cfg_t *cfg);

/** 每日唤醒计数 +1 并返回新值（NVS 持久化，用于时间未同步时的签名） */
int app_cfg_bump_wake(void);

#ifdef __cplusplus
}
#endif
#endif /* ZG_APP_CFG_H */
