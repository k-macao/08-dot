"""把运势数据排版成 Dot Text API 的文本卡片字段（title / message / signature）。

设备屏幕参考：Quote/0 为 152×296 单色墨水屏，正文保留换行（\\n）。
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

from .providers import Fortune, strip_watermark

WEEKDAY_CN = "一二三四五六日"

# index / domains 的展示标签
_LABELS = {"all": "综合", "love": "爱情", "work": "事业", "money": "财运", "health": "健康"}


def stars(score: Optional[int]) -> str:
    """1..5 分渲染为 ★★★☆☆；非法值返回空串。"""
    if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5:
        return "★" * score + "☆" * (5 - score)
    return ""


def truncate(text: str, max_chars: int) -> str:
    """按最大长度截断，尽量停在句读处。"""
    text = (text or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("。", "！", "？", "；", "，", "、", ".", "!", "?", ","):
        pos = cut.rfind(sep)
        if pos >= int(max_chars * 0.55):
            snippet = cut[: pos + 1].strip()
            if snippet and snippet[-1] in "，、；：,:.":
                snippet = snippet[:-1] + "…"
            return snippet
    return cut.rstrip() + "…"


def signature_for(day: dt.date, brand: str = "章鱼AI") -> str:
    return f"{day.year}年{day.month}月{day.day}日 星期{WEEKDAY_CN[day.weekday()]} · {brand}"


def build_card(
    fortune: Fortune,
    *,
    title: str = "章鱼AI.每日占一占",
    style: str = "brief",
    max_chars: int = 90,
    brand: str = "章鱼AI",
) -> Dict[str, str]:
    """生成 Text API 请求体中的 content 字段。

    brief（默认）：头部 + 简评/评分 + 综合运势(截断) + 四维指数 + 宜忌 + 幸运信息
    full：在 brief 基础上追加爱情/事业/财运/健康四段详细文本
    """
    lines = [f"{fortune.sign.symbol} {fortune.sign.cn} · {fortune.period}"]

    # 简评 + 星级 + 综合指数（简评与星级紧挨着）
    comment = strip_watermark(fortune.short_comment)
    star_text = stars(fortune.scores.get("all"))
    index_all = fortune.indices.get("all", "")
    meta = f"「{comment}」" if comment else ""
    if star_text:
        meta += star_text
    if index_all:
        meta = f"{meta} {index_all}".strip()
    if meta:
        lines.append(meta)

    # 综合运势正文
    overall = truncate(strip_watermark(fortune.overall), max_chars)
    if overall:
        lines.append(overall)

    # 四维指数
    dim_parts = []
    for key in ("love", "work", "money", "health"):
        value = fortune.indices.get(key, "")
        if value:
            dim_parts.append(f"{_LABELS[key]} {value}")
    if dim_parts:
        lines.append(" · ".join(dim_parts))

    # 宜 / 忌（数据源有的自带「宜/忌」前缀，统一为「宜 xxx」）
    def _todo(word: str, raw: str) -> str:
        text = strip_watermark(raw)
        if text.startswith(word):
            text = text[1:].lstrip()
        return f"{word} {text}".strip() if text else ""

    todo_parts = [part for part in (_todo("宜", fortune.yi), _todo("忌", fortune.ji)) if part]
    if todo_parts:
        lines.append(" · ".join(todo_parts))

    # 幸运信息
    lucky_parts = []
    if fortune.lucky_color:
        lucky_parts.append(strip_watermark(fortune.lucky_color))
    if fortune.lucky_number:
        lucky_parts.append(strip_watermark(fortune.lucky_number))
    if fortune.lucky_buddy:
        lucky_parts.append(f"贵人 {strip_watermark(fortune.lucky_buddy)}")
    if lucky_parts:
        lines.append("幸运 " + " · ".join(lucky_parts))

    # full 样式：追加各分项详细文本
    if style == "full":
        for key in ("love", "work", "money", "health"):
            text = fortune.domains.get(key, "")
            if text:
                detail = truncate(strip_watermark(text), max_chars)
                if detail:
                    lines.append(f"{_LABELS[key]} {detail}")

    message = "\n".join(line for line in lines if line)
    return {
        "title": title,
        "message": message,
        "signature": signature_for(fortune.date, brand=brand),
    }
