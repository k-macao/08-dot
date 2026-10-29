"""Host-side regression tests for the firmware's fixed-Leo NVS migration.

Compiles the real app_cfg.c against a tiny in-memory NVS stub. No ESP-IDF or
hardware is required; the test skips when a host C compiler is unavailable.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware" / "main"

STUBS = {
    "esp_err.h": r"""
#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
static inline const char *esp_err_to_name(esp_err_t err) {
    return err == ESP_OK ? "OK" : "FAIL";
}
""",
    "esp_log.h": r"""
#pragma once
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
""",
    "sdkconfig.h": r"""
#pragma once
#define CONFIG_ZHANGYU_WIFI_SSID ""
#define CONFIG_ZHANGYU_WIFI_PASSWORD ""
""",
    "nvs.h": r"""
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "esp_err.h"
typedef int nvs_handle_t;
typedef enum { NVS_READONLY, NVS_READWRITE } nvs_open_mode_t;
#define ESP_ERR_NVS_NOT_FOUND -100

typedef struct { char ssid[32], pass[64], smode[8], sign[12]; } fake_nvs_t;
extern fake_nvs_t store;
extern int commit_count;
extern int fail_writes;

static inline esp_err_t nvs_open(const char *ns, nvs_open_mode_t mode, nvs_handle_t *h) {
    (void)ns; (void)mode; *h = 1; return ESP_OK;
}
static inline void nvs_close(nvs_handle_t h) { (void)h; }
static inline char *fake_value(const char *key) {
    if (!strcmp(key, "ssid")) return store.ssid;
    if (!strcmp(key, "pass")) return store.pass;
    if (!strcmp(key, "smode")) return store.smode;
    if (!strcmp(key, "sign")) return store.sign;
    return NULL;
}
static inline esp_err_t nvs_get_str(nvs_handle_t h, const char *key, char *out, size_t *len) {
    (void)h;
    const char *value = fake_value(key);
    if (!value || !value[0]) return ESP_ERR_NVS_NOT_FOUND;
    size_t n = strlen(value) + 1;
    if (*len < n) return ESP_FAIL;
    memcpy(out, value, n); *len = n; return ESP_OK;
}
static inline esp_err_t nvs_set_str(nvs_handle_t h, const char *key, const char *value) {
    (void)h;
    if (fail_writes) return ESP_FAIL;
    char *dst = fake_value(key);
    if (!dst) return ESP_FAIL;
    size_t cap = !strcmp(key, "ssid") ? sizeof(store.ssid) :
                 !strcmp(key, "pass") ? sizeof(store.pass) :
                 !strcmp(key, "smode") ? sizeof(store.smode) : sizeof(store.sign);
    if (strlen(value) >= cap) return ESP_FAIL;
    snprintf(dst, cap, "%s", value);
    return ESP_OK;
}
static inline esp_err_t nvs_commit(nvs_handle_t h) {
    (void)h; if (fail_writes) return ESP_FAIL; commit_count++; return ESP_OK;
}
static inline esp_err_t nvs_erase_key(nvs_handle_t h, const char *key) {
    (void)h; char *dst = fake_value(key); if (dst) dst[0] = 0; return ESP_OK;
}
static inline esp_err_t nvs_get_u32(nvs_handle_t h, const char *key, uint32_t *value) {
    (void)h; (void)key; *value = 0; return ESP_OK;
}
static inline esp_err_t nvs_set_u32(nvs_handle_t h, const char *key, uint32_t value) {
    (void)h; (void)key; (void)value; return ESP_OK;
}
""",
}

HARNESS = r"""
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "app_cfg.h"
#include "nvs.h"

fake_nvs_t store;
int commit_count;
int fail_writes;

static void seed(const char *mode, const char *sign) {
    memset(&store, 0, sizeof(store));
    strcpy(store.ssid, "MyHome WiFi");
    strcpy(store.pass, "keep-this-password");
    strcpy(store.smode, mode);
    strcpy(store.sign, sign);
    commit_count = 0;
    fail_writes = 0;
}

static void assert_leo_and_wifi(const app_cfg_t *cfg) {
    assert(strcmp(cfg->sign_mode, "fixed") == 0);
    assert(strcmp(cfg->sign, "leo") == 0);
    assert(strcmp(cfg->ssid, "MyHome WiFi") == 0);
    assert(strcmp(cfg->pass, "keep-this-password") == 0);
}

int main(void) {
    app_cfg_t cfg;
    const char *modes[] = {"random", "fixed", ""};
    const char *signs[] = {"aries", "virgo", ""};
    for (int i = 0; i < 3; i++) {
        seed(modes[i], signs[i]);
        assert(app_cfg_load(&cfg) == ESP_OK);
        assert_leo_and_wifi(&cfg);
        assert(commit_count == 1);
        assert(strcmp(store.smode, "fixed") == 0);
        assert(strcmp(store.sign, "leo") == 0);
        assert(app_cfg_load(&cfg) == ESP_OK);
        assert_leo_and_wifi(&cfg);
        assert(commit_count == 1); /* no flash write on an already migrated boot */
    }

    seed("random", "aries");
    fail_writes = 1;
    assert(app_cfg_load(&cfg) == ESP_OK);
    assert_leo_and_wifi(&cfg); /* failed persistence must not restore random */
    fail_writes = 0;

    seed("fixed", "leo");
    assert(app_cfg_load(&cfg) == ESP_OK);
    assert(app_cfg_forget(&cfg) == ESP_OK);
    assert(cfg.ssid[0] == 0 && cfg.pass[0] == 0);
    assert(strcmp(cfg.sign, "leo") == 0);
    assert(app_cfg_load(&cfg) == ESP_OK);
    assert(strcmp(cfg.sign, "leo") == 0);
    puts("fixed-Leo NVS migration: PASS");
    return 0;
}
"""


@unittest.skipUnless(shutil.which("gcc"), "host gcc is required")
class FirmwareConfigTest(unittest.TestCase):
    def test_legacy_nvs_migrates_to_leo_without_losing_wifi(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for name, text in STUBS.items():
                (tmp / name).write_text(text, encoding="utf-8")
            source = tmp / "harness.c"
            exe = tmp / "harness"
            source.write_text(HARNESS, encoding="utf-8")
            subprocess.run(
                ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
                 "-I", str(tmp), "-I", str(MAIN), str(source), str(MAIN / "app_cfg.c"),
                 "-o", str(exe)],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run(
                [str(exe)], check=True, capture_output=True, text=True, timeout=15
            )
            self.assertIn("fixed-Leo NVS migration: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
