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
    char sign_mode[8]; /* "random" | "fixed" */
    char sign[12];     /* fixed 时: 英文 key */
} app_cfg_t;

/** 从 NVS 读取；为空时用 menuconfig 默认值播种并写回 */
esp_err_t app_cfg_load(app_cfg_t *cfg);

/** 保存到 NVS */
esp_err_t app_cfg_save(const app_cfg_t *cfg);

/** 清除 WiFi 与星座配置（回到配网模式） */
esp_err_t app_cfg_forget(app_cfg_t *cfg);

/** 每日唤醒计数 +1 并返回新值（NVS 持久化，用于时间未同步时的签名） */
int app_cfg_bump_wake(void);

#ifdef __cplusplus
}
#endif
#endif /* ZG_APP_CFG_H */
