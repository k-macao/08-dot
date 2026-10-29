/**
 * @file cli.c
 * @brief USB 串口配置命令行（首次启动配网，星座固定狮子座）
 */
#include "cli.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "fortune.h"

static const char *TAG = "cli";

/* 读一行 (带回显; 支持退格; 兼容 \r / \n / \r\n) */
static int read_line(char *buf, int cap)
{
    int n = 0;
    for (;;) {
        int ch = getchar();
        if (ch == EOF) {
            continue;
        }
        if (ch == '\r' || ch == '\n') {
            if (n == 0) {
                continue; /* CRLF 的第二半 */
            }
            putchar('\n');
            buf[n] = 0;
            return n;
        }
        if (ch == 0x7F || ch == 0x08) {
            /* 按「一个 UTF-8 字符」退格, 否则删中文会剩下半个字节 */
            if (n > 0) {
                do {
                    n--;
                    fputs("\b \b", stdout);
                } while (n > 0 && ((unsigned char)buf[n] & 0xC0) == 0x80);
            }
            continue;
        }
        /* 接受 0x20 以上的全部字节 —— 含 UTF-8 多字节(中文星座名如「狮子座」)。
         * 旧实现只放行 < 0x7F, 导致屏幕提示的 `sign 狮子座` 根本敲不进去。 */
        if (ch >= 0x20 && ch != 0x7F && n < cap - 1) {
            buf[n++] = (char)ch;
            putchar(ch);
        }
        fflush(stdout);
    }
}

static void print_banner(const app_cfg_t *cfg)
{
    printf("\n");
    printf("============================================\n");
    printf(" 章鱼AI.每日占一占 — 串口配置\n");
    printf(" 当前: ssid=%s mode=%s sign=%s\n",
           cfg->ssid[0] ? cfg->ssid : "(未设置)", cfg->sign_mode, cfg->sign);
    printf(" 命令:\n");
    printf("   wifi <SSID> [密码]   保存 WiFi 并开始占卜 (同一行用空格分隔)\n");
    printf("   sign <leo|狮子座>    确认固定星座 (不再支持抽签)\n");
    printf("   show                 显示当前配置\n");
    printf("   forget               清除 WiFi (回到配网)\n");
    printf("   go                   立即执行(已配置时)\n");
    printf("============================================\n");
}

void cli_run(app_cfg_t *cfg)
{
    print_banner(cfg);
    char line[144];
    for (;;) {
        printf("zhangyu> ");
        fflush(stdout);
        if (read_line(line, sizeof(line)) <= 0) {
            continue;
        }

        char *cmd = strtok(line, " ");
        if (!cmd) {
            continue;
        }
        char *rest = strtok(NULL, "");
        if (strcmp(cmd, "help") == 0) {
            print_banner(cfg);
        } else if (strcmp(cmd, "show") == 0) {
            printf("ssid=%s mode=%s sign=%s(%s)\n", cfg->ssid[0] ? cfg->ssid : "-",
                   cfg->sign_mode, cfg->sign,
                   fortune_sign_index(cfg->sign) >= 0
                       ? fortune_sign_cn(fortune_sign_index(cfg->sign))
                       : "?");
        } else if (strcmp(cmd, "forget") == 0) {
            app_cfg_forget(cfg);
            printf("已清除 WiFi 配置\n");
        } else if (strcmp(cmd, "go") == 0) {
            if (cfg->ssid[0]) {
                printf("开始执行每日占卜...\n");
                return;
            }
            printf("尚未配置 WiFi, 请先: wifi <SSID> [密码]\n");
        } else if (strcmp(cmd, "sign") == 0) {
            if (!rest || !rest[0]) {
                printf("用法: sign <leo|狮子座>\n");
                continue;
            }
            if (fortune_sign_index(rest) != DISPLAY_SIGN_INDEX) {
                printf("星座固定为狮子座，不支持 random 或其他星座\n");
                continue;
            }
            printf("已固定: 狮子座 (leo)，无需重复设置\n");
        } else if (strcmp(cmd, "wifi") == 0) {
            if (!rest || !rest[0]) {
                printf("用法: wifi <SSID> [密码]\n");
                continue;
            }
            char *sp = strchr(rest, ' ');
            if (sp) {
                *sp = 0;
                while (*(sp + 1) == ' ') {
                    sp++;
                }
                snprintf(cfg->pass, sizeof(cfg->pass), "%s", sp + 1);
            } else {
                cfg->pass[0] = 0;
            }
            if (strlen(rest) >= sizeof(cfg->ssid)) {
                printf("SSID 过长 (≤31 字节)\n");
                continue;
            }
            snprintf(cfg->ssid, sizeof(cfg->ssid), "%s", rest);
            if (app_cfg_save(cfg) != ESP_OK) {
                printf("保存失败 (NVS 错误)\n");
                continue;
            }
            ESP_LOGI(TAG, "WiFi 已保存: %s", cfg->ssid);
            printf("WiFi 已保存, 开始联网占卜...\n");
            return;
        } else {
            printf("未知命令 %s (输入 help 查看)\n", cmd);
        }
    }
}
