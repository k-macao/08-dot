#!/usr/bin/env python3
"""生成 16px 像素字库 C 源码（文泉驿微米黑 → GB2312 常用集）。

字体: WenQuanYi Micro Hei (wqy-microhei.ttc)
  授权: Apache-2.0 / GPLv3 双许可（可嵌入固件）
  来源: https://github.com/anthonyfok/fonts-wqy-microhei

字符集: ASCII(0x20-0x7E) + GB2312 符号区(A1/A3) + GB2312 一级汉字(3755)
输出:   font_gen.c / font_gen.h（按码点升序，二分查找）

用法:
    python3 tools/gen_font.py --fetch      # 自动下载字体后生成
    python3 tools/gen_font.py --font xxx.ttc
    python3 tools/gen_font.py --preview 章鱼AI  # 打印样例点阵
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import tarfile
import urllib.request
from pathlib import Path

FONT_URL = "https://codeload.github.com/anthonyfok/fonts-wqy-microhei/tar.gz/refs/heads/master"
FONT_MEMBER = "fonts-wqy-microhei-master/wqy-microhei.ttc"
SIZE = 16
BASELINE = 15  # 顶行 0..15，基线在 15

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent / "components" / "render"
CACHE = HERE / ".cache"


def charset() -> list[str]:
    """ASCII + GB2312 A1/A3 符号区 + 一级汉字。"""
    import codecs

    gb = codecs.lookup("gb2312")
    chars: set[str] = {chr(c) for c in range(0x20, 0x7F)}
    zones = [(0xA1, 0xA2), (0xA3, 0xA4)] + [(0xB0, 0xD8)]
    for lo, hi in zones:
        for b0 in range(lo, hi):
            for b1 in range(0xA1, 0xFF):
                try:
                    ch = bytes((b0, b1)).decode("gb2312")
                except UnicodeDecodeError:
                    continue
                chars.add(ch)
    # 码点不在 GB2312 一级字库、但卡片文案真的会用到的字符。
    # 这些字必须显式加入，否则设备上会画成空心方框：
    #   羯 —— 「摩羯座」(星座名, fortune.c kCn)
    #   珀 琥 —— 「琥珀金」(离线引擎幸运色, fortune.c kColors)
    #   黛 —— 「黛蓝」(离线引擎幸运色, fortune.c kColors)
    # 排版分隔符: · (U+00B7)、— (U+2014)
    EXTRA = "·—羯珀琥黛"
    chars.update(EXTRA)
    # 确保 GB2312 可编码（一级汉字之外的字符丢弃）；EXTRA 白名单除外
    out = []
    for ch in chars:
        if ch in EXTRA:
            out.append(ch)
            continue
        try:
            gb.encode(ch)
        except UnicodeEncodeError:
            continue
        out.append(ch)
    out.sort(key=lambda c: ord(c))
    return out


def fetch_font() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    ttc = CACHE / "wqy-microhei.ttc"
    if ttc.exists():
        return ttc
    print(f"下载字体 {FONT_URL} …")
    with urllib.request.urlopen(FONT_URL, timeout=60) as resp:
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.extractfile(FONT_MEMBER)
        if member is None:
            raise SystemExit(f"压缩包内未找到 {FONT_MEMBER}")
        ttc.write_bytes(member.read())
    print(f"已缓存 → {ttc}")
    return ttc


def render_glyph(font, ch: str):
    """返回 (width, 32 字节点阵)。位序: 每行 2 字节, bit7=最左像素。"""
    from PIL import Image, ImageDraw

    adv = font.getlength(ch)
    w = max(1, min(SIZE, int(math.ceil(adv))))
    img = Image.new("L", (SIZE, SIZE), 255)
    draw = ImageDraw.Draw(img)
    draw.text((0, BASELINE), ch, font=font, fill=0, anchor="ls")
    px = img.load()
    bits = bytearray(32)
    for y in range(SIZE):
        for x in range(w):
            if px[x, y] < 128:
                if x < 8:
                    bits[y * 2] |= 0x80 >> x
                else:
                    bits[y * 2 + 1] |= 0x80 >> (x - 8)
    return w, bytes(bits)


def preview(font, text: str) -> None:
    for ch in text:
        w, bits = render_glyph(font, ch)
        print(f"--- {ch!r} (w={w}) ---")
        for y in range(SIZE):
            row = []
            for x in range(w):
                byte = bits[y * 2 + (x // 8)]
                row.append("#" if byte & (0x80 >> (x % 8)) else ".")
            print("".join(row))


def emit(chars: list[str], glyphs: list[tuple[int, bytes]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    header = f"""/* 自动生成 — 请勿手改 (tools/gen_font.py)
 * 字体: WenQuanYi Micro Hei 16px  (Apache-2.0 / GPLv3)
 * 字符集: ASCII + GB2312 符号区(A1/A3) + 一级汉字, 共 {len(chars)} 字
 */
#ifndef ZG_FONT_GEN_H
#define ZG_FONT_GEN_H
#include <stdint.h>
#define ZG_FONT_COUNT   {len(chars)}
#define ZG_FONT_SIZE    {SIZE}
#define ZG_FONT_BASELINE {BASELINE}
extern const uint16_t zg_font_cps[ZG_FONT_COUNT];
extern const uint8_t  zg_font_widths[ZG_FONT_COUNT];
extern const uint8_t  zg_font_bits[ZG_FONT_COUNT * 32];
#endif
"""
    (OUT_DIR / "font_gen.h").write_text(header, encoding="utf-8")

    out = io.StringIO()
    out.write(f"/* 自动生成 — 请勿手改 (tools/gen_font.py) — {len(chars)} glyphs */\n")
    out.write('#include "font_gen.h"\n\n')

    out.write("const uint16_t zg_font_cps[ZG_FONT_COUNT] = {\n")
    line = []
    for i, ch in enumerate(chars):
        line.append(f"0x{ord(ch):04X}")
        if len(line) == 16:
            out.write("    " + ", ".join(line) + ",\n")
            line = []
    if line:
        out.write("    " + ", ".join(line) + ",\n")
    out.write("};\n\n")

    out.write("const uint8_t zg_font_widths[ZG_FONT_COUNT] = {\n")
    line = []
    for w, _ in glyphs:
        line.append(str(w))
        if len(line) == 24:
            out.write("    " + ", ".join(line) + ",\n")
            line = []
    if line:
        out.write("    " + ", ".join(line) + ",\n")
    out.write("};\n\n")

    out.write("const uint8_t zg_font_bits[ZG_FONT_COUNT * 32] = {\n")
    for w, bits in glyphs:
        out.write("    " + ", ".join(str(b) for b in bits) + ",\n")
    out.write("};\n")

    (OUT_DIR / "font_gen.c").write_text(out.getvalue(), encoding="utf-8")
    print(f"生成 {OUT_DIR / 'font_gen.c'} ({len(chars)} 字, "
          f"{(OUT_DIR / 'font_gen.c').stat().st_size // 1024} KB)")
    print(f"生成 {OUT_DIR / 'font_gen.h'}")


SRC_DIR = HERE.parent / "main"


def loaded_charset() -> set[str]:
    """读取已生成的 font_gen.c 里的码点表 (设备实际可用字形)。"""
    import re

    src = OUT_DIR / "font_gen.c"
    if not src.exists():
        return set()
    text = src.read_text(encoding="utf-8")
    if "zg_font_cps" not in text:
        return set()
    body = text.split("zg_font_cps", 1)[1].split("};", 1)[0]
    return {chr(int(m, 16)) for m in re.findall(r"0x([0-9A-Fa-f]{4})", body)}


def check_sources() -> int:
    """扫描 firmware/main/*.c 的字符串字面量, 报告字库里缺失的字符。

    注释会被剔除（注释不出现在屏幕上），因此不会误报。
    """
    import re

    # 以「已提交的字库」为准 (它是设备上真正渲染用的数据); 若尚未生成则退回
    # charset() 的规划字符集。
    have = loaded_charset() or set(charset())
    missing: dict[str, set[str]] = {}
    for path in sorted(SRC_DIR.glob("*.c")):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        for lit in re.findall(r'"((?:[^"\\\n]|\\.)*)"', text):
            for ch in lit:
                if ord(ch) > 0x7E and ch not in have:
                    missing.setdefault(path.name, set()).add(ch)
    if not missing:
        print(f"字库覆盖检查通过 ({len(have)} 字, 源码字面量全部覆盖)")
        return 0
    for name, chars in missing.items():
        print(f"✗ {name} 缺字: " + "".join(sorted(chars)) +
              "  → 请把它们加入 gen_font.py 的 EXTRA 后重新生成")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", help="本地 ttc/ttf 路径")
    ap.add_argument("--fetch", action="store_true", help="自动下载 wqy-microhei.ttc")
    ap.add_argument("--preview", help="打印样例字符点阵后退出")
    ap.add_argument("--check", action="store_true",
                    help="只检查固件文案是否有缺字(不需要 Pillow/字体文件)")
    args = ap.parse_args()

    if args.check:
        return check_sources()

    from PIL import ImageFont

    if args.font:
        path = Path(args.font)
    elif args.fetch or not (CACHE / "wqy-microhei.ttc").exists():
        path = fetch_font()
    else:
        path = CACHE / "wqy-microhei.ttc"

    font = ImageFont.truetype(str(path), SIZE, index=0)

    if args.preview:
        preview(font, args.preview)
        return 0

    chars = charset()
    glyphs = [render_glyph(font, ch) for ch in chars]
    emit(chars, glyphs)

    # 空字形检查（不应出现未覆盖字符）
    empty = [ch for ch, (_, bits) in zip(chars, glyphs) if all(b == 0 for b in bits)
             if ch.strip()]
    print(f"空白字形: {len(empty)} {'(含空格属正常)' if empty else ''}")
    if empty[:10]:
        print("  例:", "".join(empty[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
