#ifndef ZG_CLI_H
#define ZG_CLI_H

#include "app_cfg.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * USB 串口配置命令行 (仅未配置 WiFi 时进入)。
 * 阻塞直到: `wifi ...` 保存成功, 或已配置时输入 `go`。
 * 命令: wifi <SSID> [密码] / sign <leo|狮子座> / show / forget / go / help
 */
void cli_run(app_cfg_t *cfg);

#ifdef __cplusplus
}
#endif
#endif /* ZG_CLI_H */
