"""十二星座元数据与名称解析。

`type` 参数取值参考线上接口文档（英文小写）：
    aries, taurus, gemini, cancer, leo, virgo,
    libra, scorpio, sagittarius, capricorn, aquarius, pisces
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Sign:
    key: str  # 英文小写，线上接口参数值
    cn: str  # 中文名，如 "白羊座"
    symbol: str  # 星座符号
    date_range: str  # 日期范围


SIGNS: tuple = (
    Sign("aries", "白羊座", "♈", "3.21-4.19"),
    Sign("taurus", "金牛座", "♉", "4.20-5.20"),
    Sign("gemini", "双子座", "♊", "5.21-6.21"),
    Sign("cancer", "巨蟹座", "♋", "6.22-7.22"),
    Sign("leo", "狮子座", "♌", "7.23-8.22"),
    Sign("virgo", "处女座", "♍", "8.23-9.22"),
    Sign("libra", "天秤座", "♎", "9.23-10.23"),
    Sign("scorpio", "天蝎座", "♏", "10.24-11.22"),
    Sign("sagittarius", "射手座", "♐", "11.23-12.21"),
    Sign("capricorn", "摩羯座", "♑", "12.22-1.19"),
    Sign("aquarius", "水瓶座", "♒", "1.20-2.18"),
    Sign("pisces", "双鱼座", "♓", "2.19-3.20"),
)

# 额外中文别名（常见异写）
_EXTRA_ALIASES = {
    "牧羊座": "aries",
    "牡羊座": "aries",
    "魔羯座": "capricorn",
    "山羊座": "capricorn",
    "天平座": "libra",
    "蝎座": "scorpio",
}


def _build_alias_index() -> Dict[str, Sign]:
    index: Dict[str, Sign] = {}
    for sign in SIGNS:
        aliases = {
            sign.key,
            sign.key.capitalize(),
            sign.cn,
            sign.cn[:-1] if sign.cn.endswith("座") else sign.cn,
            sign.symbol,
        }
        for alias in aliases:
            index[alias.lower()] = sign
    for alias, key in _EXTRA_ALIASES.items():
        index[alias.lower()] = get_sign(key)
    return index


def get_sign(key: str) -> Sign:
    """按英文 key 取星座。"""
    for sign in SIGNS:
        if sign.key == key:
            return sign
    raise ValueError(f"未知星座 key: {key!r}")


def parse_sign(value: str) -> Sign:
    """按任意受支持的写法解析星座：英文 / 中文 / 简称 / 符号。"""
    if value is None:
        raise ValueError("星座不能为空")
    normalized = str(value).strip().lower()
    if not normalized:
        raise ValueError("星座不能为空")
    index = _ALIAS_INDEX
    sign = index.get(normalized)
    if sign is None:
        # 模糊兜底：去空格与标点后再试一次
        compact = "".join(ch for ch in normalized if ch.isalnum() or ord(ch) > 127)
        sign = index.get(compact)
    if sign is None:
        valid = ", ".join(s.key for s in SIGNS)
        raise ValueError(f"无法识别星座 {value!r}，可用值: {valid}（也支持中文名/符号）")
    return sign


def random_sign(seed: Optional[str] = None) -> Sign:
    """由种子决定的“抽签”星座；同一种子结果稳定（用于每日一次的随机占卜）。"""
    rng = random.Random(seed)
    return rng.choice(SIGNS)


_ALIAS_INDEX = _build_alias_index()
