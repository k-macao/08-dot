"""把指数行情排版成 Dot Text API 的文本卡片字段（title / message / signature）。

设备屏幕参考：Quote/0 为 152×296 单色墨水屏，正文保留换行（\\n）。
卡片结构（10 项指数分三组，每组一个小节标题 + 一行一项）：

    A股
    上证 3888.12 +0.56%
    …
    港股
    恒指 24567.80 +0.31%
    …
    美股
    道琼斯 47123.45 -0.12%
    …
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence

from .quotes import GROUP_ORDER, Quote, QuoteSet

WEEKDAY_CN = "一二三四五六日"


def format_price(price: float) -> str:
    """指数点位：常规保留 2 位小数；超大点位自动去尾零避免行宽溢出。"""
    if price >= 100000:  # 例如 123456.78 → 123457
        return f"{price:.0f}"
    return f"{price:.2f}"


def format_pct(pct: Optional[float]) -> str:
    """涨跌幅：始终带符号（+0.56% / -0.12% / 0.00%）；未知显示 '--'。"""
    if pct is None:
        return "--"
    return f"{pct:+.2f}%"


def quote_line(quote: Quote) -> str:
    """一行一项：「上证 3888.12 +0.56%」。"""
    return f"{quote.label} {format_price(quote.price)} {format_pct(quote.pct)}"


def signature_for(moment: dt.datetime, brand: str = "章鱼AI") -> str:
    return (
        f"{moment.year}年{moment.month}月{moment.day}日 "
        f"星期{WEEKDAY_CN[moment.weekday()]} {moment:%H:%M} · {brand}"
    )


def build_card(
    quoteset: QuoteSet,
    *,
    title: str = "章鱼AI.行情速报",
    brand: str = "章鱼AI",
    moment: Optional[dt.datetime] = None,
) -> Dict[str, str]:
    """生成 Text API 请求体中的 content 字段。

    message 按 GROUP_ORDER 分组输出；某组一项都没有时整组跳过。
    moment 缺省用本地当前时间（签名栏显示取数时刻）。
    """
    by_group: Dict[str, List[Quote]] = {}
    for quote in quoteset.quotes:
        by_group.setdefault(quote.group, []).append(quote)

    lines: List[str] = []
    for group in GROUP_ORDER:
        rows = by_group.get(group) or []
        if not rows:
            continue
        lines.append(group)
        lines.extend(quote_line(q) for q in rows)

    if not lines:  # 兜底：调用方应已拦截空数据，这里保证 message 非空
        lines.append("暂无行情数据")

    moment = moment or dt.datetime.now()
    return {
        "title": title,
        "message": "\n".join(lines),
        "signature": signature_for(moment, brand=brand),
    }
