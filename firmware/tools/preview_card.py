#!/usr/bin/env python3
"""在电脑上把固件卡片渲染成 PNG（不接硬件、不需要 ESP-IDF）。

做法：用系统 gcc 把 `components/render/fb.c`、`font_gen.c` 和 `main/card.c`
编译成一个主机程序，链接一个「假墨水屏」驱动（把帧缓冲写成 PNG），
因此所见即真实固件排版 —— 用来在烧机前核对断行、留白、缺字。

用法:
    python3 firmware/tools/preview_card.py                 # 配网卡 + 示例运势卡
    python3 firmware/tools/preview_card.py --out /tmp/png   # 指定输出目录
    python3 firmware/tools/preview_card.py --widths        # 只打印字宽(改文案时用)
    python3 firmware/tools/preview_card.py --text "sign 狮子座 固定"  # 量指定文本

依赖: gcc + python3 标准库（PNG 用 zlib 手写，无需 Pillow）。
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FW = HERE.parent
RENDER = FW / "components" / "render"
MAIN = FW / "main"
OUT_DIR = FW / "build" / "preview"

# ---------------------------------------------------------------- 主机桩 --
STUB_ESP_ERR = """#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
const char *esp_err_to_name(esp_err_t e);
"""

STUB_ESP_LOG = """#pragma once
#include <stdio.h>
#define ESP_LOGI(t, ...) do { printf("[I %s] ", t); printf(__VA_ARGS__); printf("\\n"); } while (0)
#define ESP_LOGW(t, ...) do { printf("[W %s] ", t); printf(__VA_ARGS__); printf("\\n"); } while (0)
#define ESP_LOGE(t, ...) do { printf("[E %s] ", t); printf(__VA_ARGS__); printf("\\n"); } while (0)
"""

STUB_EPD_H = """#pragma once
#include <stdint.h>
#include "esp_err.h"
#define EPD_WIDTH 152
#define EPD_HEIGHT 296
#define EPD_BUFFER_SIZE ((EPD_WIDTH * EPD_HEIGHT) / 8)
esp_err_t epd_init(void);
esp_err_t epd_show(const uint8_t *data);
esp_err_t epd_clear_white(void);
esp_err_t epd_sleep(void);
"""

STUB_EPD_C = r"""#include "epd.h"
#include <stdio.h>
#include <string.h>

static unsigned g_frame = 0;

const char *esp_err_to_name(esp_err_t e) { return e == 0 ? "OK" : "FAIL"; }
esp_err_t epd_init(void) { return ESP_OK; }
esp_err_t epd_clear_white(void) { return ESP_OK; }
esp_err_t epd_sleep(void) { return ESP_OK; }

esp_err_t epd_show(const uint8_t *data)
{
    printf("FRAME %u\n", g_frame++);
    fwrite(data, 1, EPD_BUFFER_SIZE, stdout);
    fflush(stdout);
    return ESP_OK;
}
"""

HARNESS = r"""#include <string.h>
#include "card.h"
#include "fortune.h"

/* 1) 配网卡（首次开机/清空配置后看到的就是这一张） */
static void case_setup(void)
{
    card_show_setup("章鱼AI.每日占一占");
}

/* 2) 正常运势卡（中文数据源 xxapi） */
static void case_daily_cn(void)
{
    fortune_t f;
    memset(&f, 0, sizeof f);
    strcpy(f.sign_key, "leo");
    strcpy(f.sign_cn, "狮子座");
    strcpy(f.period, "今日运势");
    f.score = 4;
    strcpy(f.idx, "88%");
    strcpy(f.love, "72%");
    strcpy(f.work, "84%");
    strcpy(f.money, "61%");
    strcpy(f.health, "77%");
    strcpy(f.overall,
           "今日整体运势平稳向上，适合推进手头的工作与计划。沟通顺畅，"
           "主动表达想法会得到认可。晚间适合放松休息，避免熬夜。");
    strcpy(f.yi, "宜 主动沟通");
    strcpy(f.ji, "忌 冲动消费");
    strcpy(f.color, "草绿");
    strcpy(f.number, "17");
    strcpy(f.buddy, "白羊座");
    strcpy(f.tag, "xxapi");
    card_show(&f, "章鱼AI.每日占一占", "10月26日 周一");
}

/* 3) 英文备用源（freehoroscope）：纯 ASCII 长段落，检查按词折行 */
static void case_daily_en(void)
{
    fortune_t f;
    memset(&f, 0, sizeof f);
    strcpy(f.sign_key, "leo");
    strcpy(f.sign_cn, "狮子座");
    strcpy(f.period, "今日运势");
    strcpy(f.overall,
           "You may feel a strong urge to break free from routine today. "
           "Some unexpected news arrives mid-morning; keep your response "
           "measured and the day turns in your favour by evening.");
    strcpy(f.tag, "en备用");
    card_show(&f, "章鱼AI.每日占一占", "10月26日 周一");
}

/* 4) 离线兜底：有评分但无四维指数 / 无宜忌 */
static void case_offline(void)
{
    fortune_t f;
    memset(&f, 0, sizeof f);
    strcpy(f.sign_key, "pisces");
    strcpy(f.sign_cn, "双鱼座");
    strcpy(f.period, "今日运势");
    f.score = 3;
    strcpy(f.comment, "静待花开");
    strcpy(f.overall, "把大目标拆成三件小事，完成它们，今天就已经赢了。");
    strcpy(f.color, "松石绿");
    strcpy(f.number, "42");
    strcpy(f.buddy, "天蝎座");
    strcpy(f.tag, "离线·无网");
    card_show(&f, "章鱼AI.每日占一占", "章鱼AI 第7占");
}

int main(void)
{
    case_setup();
    case_daily_cn();
    case_daily_en();
    case_offline();
    return 0;
}
"""

WIDTH_PROBE = r"""#include <stdio.h>
#include "fb.h"

int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        printf("%4d  %s\n", fb_text_width(argv[i]), argv[i]);
    }
    return 0;
}
"""

SOURCES = ["fb.c", "font_gen.c"]


def build(tmp: Path, harness_src: str, out: Path) -> None:
    (tmp / "stub").mkdir(parents=True, exist_ok=True)
    (tmp / "stub" / "esp_err.h").write_text(STUB_ESP_ERR, encoding="utf-8")
    (tmp / "stub" / "esp_log.h").write_text(STUB_ESP_LOG, encoding="utf-8")
    (tmp / "stub" / "epd.h").write_text(STUB_EPD_H, encoding="utf-8")
    (tmp / "stub" / "epd_stub.c").write_text(STUB_EPD_C, encoding="utf-8")
    (tmp / "harness.c").write_text(harness_src, encoding="utf-8")

    cmd = [
        "gcc", "-std=c11", "-O1", "-o", str(out),
        str(tmp / "harness.c"), str(tmp / "stub" / "epd_stub.c"),
        str(MAIN / "card.c"),
        *[str(RENDER / s) for s in SOURCES],
        "-I", str(tmp / "stub"), "-I", str(RENDER), "-I", str(MAIN),
    ]
    subprocess.run(cmd, check=True)


def decode_frames(raw: bytes) -> list[bytes]:
    """把固件输出里的 FRAME 段切出来（每段 5624 字节帧缓冲）。"""
    frames: list[bytes] = []
    marker = b"FRAME "
    pos = 0
    while True:
        idx = raw.find(marker, pos)
        if idx < 0:
            break
        eol = raw.index(b"\n", idx)
        start = eol + 1
        frames.append(raw[start:start + 5624])
        pos = start + 5624
    return frames


def frame_to_png(buf: bytes, scale: int = 3) -> bytes:
    """帧缓冲(1=白 0=黑, 每行 19 字节) → 1bpp PNG。"""
    w, h = 152, 296
    rows = []
    for y in range(h):
        line = bytearray()
        for x in range(w):
            bit = (buf[y * 19 + (x >> 3)] >> (7 - (x & 7))) & 1
            line += bytes([255 if bit else 0]) * scale
        for _ in range(scale):
            rows.append(b"\x00" + bytes(line))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w * scale, h * scale,
                                      8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    return png


def main() -> int:
    ap = argparse.ArgumentParser(description="固件卡片主机端预览")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="PNG 输出目录")
    ap.add_argument("--widths", action="store_true", help="打印常用文案字宽")
    ap.add_argument("--text", nargs="*", default=None, help="量指定文本的字宽")
    ap.add_argument("--max-width", type=int, default=0,
                    help="配合 --widths: 有超宽行时以退出码 1 结束(CI 用于把守 144px)")
    args = ap.parse_args()

    if args.widths or args.text:
        texts = args.text if args.text else [
            "等待配置 WiFi", "连接 USB 到电脑", "打开串口终端", "串口 115200",
            "输入 wifi 名称 密码", "sign 狮子座 固定", "sign random 抽签",
            "输入 help 看命令", "每日 07:00 自动更新",
        ]
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            exe = tmp / "probe"
            build(tmp, WIDTH_PROBE, exe)
            out = subprocess.run([str(exe), *texts], check=True,
                                 capture_output=True).stdout.decode()
        print(out, end="")
        print("\n正文可用宽度 = 144px (152 - 2*4)")
        if args.max_width:
            over = [ln for ln in out.splitlines()
                    if ln.strip() and int(ln.split()[0]) > args.max_width]
            if over:
                print(f"\n✗ {len(over)} 行超过 {args.max_width}px:")
                for ln in over:
                    print("   " + ln)
                return 1
            print(f"✓ 所有行都在 {args.max_width}px 以内")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        exe = tmp / "harness"
        build(tmp, HARNESS, exe)
        proc = subprocess.run([str(exe)], check=True, capture_output=True)

    frames = decode_frames(proc.stdout)
    names = ["setup", "daily_cn", "daily_en", "offline"]
    for i, buf in enumerate(frames):
        name = names[i] if i < len(names) else f"frame{i}"
        path = args.out / f"{name}.png"
        path.write_bytes(frame_to_png(buf))
        print(f"已生成 {path}")
    if proc.stderr:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
