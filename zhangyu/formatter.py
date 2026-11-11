"""把指数行情排版成 Dot Text API 的文本卡片字段（title / message / signature）。

设备屏幕参考：Quote/0 为 152×296 单色墨水屏。卡片固定 6 行：

    第 1 行  标题 · 日期时间     （Text API 的 title 字段；--no-title 时
                                    该行只留日期时间，并放进 message 首行）
    第 2 行  上证 3888.12 +0.56%
    第 3 行  恒生指数 24567.89 +0.31%
    第 4 行  恒科 5678.90 -0.66%
    第 5 行  纳斯达克 19876.54 +0.25%
    第 6 行  标普五百 5890.12 +0.18%

数据层仍按 10 项指数批量取数（见 quotes.INDEX_SPECS，东财/新浪/缓存链路不变），
卡片只按 CARD_ROWS 挑选展示其中 5 项；未取到的项直接缺席该行，不编造数据。
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from .quotes import Quote, QuoteSet

# 卡片展示行：(指数键, 显示名)，顺序即卡片行序（用户指定的 5 项）
CARD_ROWS: tuple[tuple[str, str], ...] = (
    ("sh", "上证"),
    ("hsi", "恒生指数"),
    ("hstech", "恒科"),
    ("ixic", "纳斯达克"),
    ("spx", "标普五百"),
)


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


def quote_line(label: str, quote: Quote) -> str:
    """一行一项：「上证 3888.12 +0.56%」。label 用卡片显示名（非数据层短标签）。"""
    return f"{label} {format_price(quote.price)} {format_pct(quote.pct)}"


def stamp_for(moment: dt.datetime) -> str:
    """第 1 行的日期时间：「11-10 16:05」（月-日 时:分，小时级卡片足够）。"""
    return moment.strftime("%m-%d %H:%M")


def build_card(
    quoteset: QuoteSet,
    *,
    title: str = "章鱼AI.行情速报",
    moment: Optional[dt.datetime] = None,
) -> Dict[str, str]:
    """生成 Text API 请求体中的 content 字段（6 行卡片）。

    title 非空：title 字段 = 「标题 · 日期时间」，message = 5 行指数，无 signature；
    title 为空（--no-title）：title 字段省略，日期时间作为 message 首行，
    屏幕总行数仍是 6。moment 缺省用本地当前时间。
    """
    moment = moment or dt.datetime.now()
    stamp = stamp_for(moment)

    by_key: Dict[str, Quote] = {q.key: q for q in quoteset.quotes}
    lines: List[str] = []
    for key, label in CARD_ROWS:
        quote = by_key.get(key)
        if quote is not None:  # 未取到的项直接缺席，不编数据
            lines.append(quote_line(label, quote))
    if not lines:  # 兜底：调用方应已拦截空数据，这里保证 message 非空
        lines.append("暂无行情数据")

    if title:
        return {
            "title": f"{title} · {stamp}",
            "message": "\n".join(lines),
            "signature": "",
        }
    return {
        "title": "",
        "message": "\n".join([stamp, *lines]),
        "signature": "",
    }
