"""每日占星数据源。

优先级（可在配置中调整 providers 顺序）：
    1. xxapi         https://v2.xxapi.cn/api/horoscope     中文 · 免费免密 · QPS 50
    2. freehoroscope https://freehoroscopeapi.com/...      英文 · 免费免密（备用）
    3. offline       内置“章鱼AI 离线占卜引擎”              永远可用的兜底

任一线上接口失败都会自动降级，保证每日卡片必达。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signs import SIGNS, Sign, get_sign

USER_AGENT = "zhangyu-daily-divination/1.0 (+Dot Open Platform Text API)"

XXAPI_ENDPOINT = "https://v2.xxapi.cn/api/horoscope"
FREE_HOROSCOPE_ENDPOINT = "https://freehoroscopeapi.com/api/v1/get-horoscope/daily"

# 线上数据源在正文尾部追加的站点水印，例如「星o座o屋」「星#座#屋」「星8座8屋」「星座屋」
_WATERMARK_RE = re.compile(r"星.{0,3}座.{0,3}屋\s*$")


class ProviderError(RuntimeError):
    """数据源请求/解析失败（会触发向下一数据源降级）。"""


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #

@dataclass
class Fortune:
    """一条归一化后的运势数据。"""

    sign: Sign
    date: dt.date
    source: str
    period: str = "今日运势"
    overall: str = ""
    short_comment: str = ""
    scores: Dict[str, int] = field(default_factory=dict)  # all/love/work/money/health，1..5
    indices: Dict[str, str] = field(default_factory=dict)  # 百分比字符串，如 "66%"
    domains: Dict[str, str] = field(default_factory=dict)  # love/work/money/health 文本
    lucky_color: str = ""
    lucky_number: str = ""
    lucky_buddy: str = ""
    yi: str = ""
    ji: str = ""
    offline: bool = False


def strip_watermark(text: str) -> str:
    """去掉数据源附加的站点水印并规整空白。"""
    if not text:
        return ""
    cleaned = _WATERMARK_RE.sub("", text.strip())
    return re.sub(r"\s+", " ", cleaned).strip()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:  # HTTP 状态错误
        raise ProviderError(f"HTTP {exc.code} @ {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:  # 网络错误
        raise ProviderError(f"网络错误 @ {url}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"响应不是合法 JSON @ {url}") from exc
    if not isinstance(payload, dict):
        raise ProviderError(f"响应结构异常 @ {url}")
    return payload


# --------------------------------------------------------------------------- #
# 数据源 1：xxapi（中文，主用）
# --------------------------------------------------------------------------- #

def parse_xxapi(payload: dict, sign: Sign, date: dt.date) -> Fortune:
    """解析 v2.xxapi.cn/api/horoscope 的响应（便于离线 fixture 测试）。"""
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise ProviderError(f"xxapi 返回异常: {payload.get('msg')!r}")

    data = payload["data"]
    # 上游返回了另一个星座时不能贴错标签；拒绝该响应，让统一入口降级。
    # 未提供名称时沿用请求星座，保持对旧接口响应的兼容。
    try:
        actual = get_sign(str(data.get("name", "")).strip()) if data.get("name") else sign
    except ValueError:
        actual = sign
    if actual.key != sign.key:
        raise ProviderError(f"xxapi 返回星座 {actual.cn} 与请求的 {sign.cn} 不符")

    scores_raw = data.get("fortune") or {}
    indices_raw = data.get("index") or {}
    texts_raw = data.get("fortunetext") or {}
    todo = data.get("todo") or {}

    scores = {
        key: int(scores_raw[key])
        for key in ("all", "love", "work", "money", "health")
        if isinstance(scores_raw.get(key), (int, float))
    }
    indices = {
        key: str(indices_raw[key]).strip()
        for key in ("all", "love", "work", "money", "health")
        if indices_raw.get(key)
    }
    domains = {
        key: strip_watermark(str(texts_raw[key]))
        for key in ("love", "work", "money", "health")
        if texts_raw.get(key)
    }

    return Fortune(
        sign=actual,
        date=date,
        source="xxapi（小小API·中文）",
        period=str(data.get("type") or "今日运势"),
        overall=strip_watermark(str(data.get("fortunetext", {}).get("all") or "")),
        short_comment=strip_watermark(str(data.get("shortcomment") or "")),
        scores=scores,
        indices=indices,
        domains=domains,
        lucky_color=strip_watermark(str(data.get("luckycolor") or "")),
        lucky_number=strip_watermark(str(data.get("luckynumber") or "")),
        lucky_buddy=strip_watermark(str(data.get("luckyconstellation") or "")),
        yi=strip_watermark(str(todo.get("yi") or "")),
        ji=strip_watermark(str(todo.get("ji") or "")),
    )


def fetch_xxapi(sign: Sign, date: dt.date, timeout: float = 15.0) -> Fortune:
    query = urlencode({"type": sign.key, "time": "today"})
    payload = _http_get_json(f"{XXAPI_ENDPOINT}?{query}", timeout=timeout)
    return parse_xxapi(payload, sign, date)


# --------------------------------------------------------------------------- #
# 数据源 2：Free Horoscope API（英文，备用）
# --------------------------------------------------------------------------- #

def parse_freehoroscope(payload: dict, sign: Sign, date: dt.date) -> Fortune:
    """解析 freehoroscopeapi.com 的响应。"""
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("horoscope"):
        raise ProviderError(f"freehoroscope 返回异常: {payload!r}")
    return Fortune(
        sign=sign,
        date=date,
        source="Free Horoscope API（en）",
        overall=str(data["horoscope"]).strip(),
    )


def fetch_freehoroscope(sign: Sign, date: dt.date, timeout: float = 15.0) -> Fortune:
    query = urlencode({"sign": sign.key})
    payload = _http_get_json(f"{FREE_HOROSCOPE_ENDPOINT}?{query}", timeout=timeout)
    return parse_freehoroscope(payload, sign, date)


# --------------------------------------------------------------------------- #
# 数据源 3：内置“章鱼AI 离线占卜引擎”（兜底，永远可用）
# --------------------------------------------------------------------------- #

_COLORS = [
    "黛蓝", "琥珀金", "松石绿", "樱粉", "月白", "朱砂", "墨黑", "草绿",
    "藕荷", "杏黄", "靛青", "银灰",
]
_YI = [
    "整理桌面与计划", "读几页闲书", "给绿植浇水", "出门散步晒太阳",
    "联系久未问候的朋友", "提早十分钟出门", "尝试一条新路线", "记录今天的小确幸",
    "把手机放远一点", "整理相册与文件", "听一张完整的专辑", "做一顿简单的饭",
]
_JI = [
    "熬夜刷手机", "冲动消费", "临时改主意", "久坐不起",
    "替人做承诺", "空腹喝浓茶", "把话说太满", "临期赶工",
    "深夜做重大决定", "带情绪回消息", "忽略午休", "一次性揽下太多事",
]
_COMMENTS = [
    "稳中求进", "厚积薄发", "顺水行舟", "静待花开",
    "蓄势待发", "温故知新", "以柔克刚", "水到渠成",
    "守正出奇", "步步为营", "轻装上阵", "曲径通幽",
]
_OVERALLS = [
    "今日星象平顺，把注意力放回手头最重要的一件事，节奏稳了，好运自然跟上。",
    "宇宙给你留了一扇小窗：先完成再完美，午后会迎来一个小小的转机。",
    "能量偏向内敛的一天，适合梳理与复盘；你默默做的准备，别人开始看见。",
    "今日贵人在侧，主动开口会得到回应；重要决定放在头脑最清醒的时段。",
    "别急着向所有人解释自己，留白也是力量；傍晚适合独处回血。",
    "小麻烦只是路过，不是停留；按自己的步调走，虚惊一场之后是坦途。",
    "灵感比执行力更旺的一天，先记下点子，再挑最容易的一件动手。",
    "人际磁场升温，一句真诚的夸赞能打开新局面；财务上宜守不宜攻。",
    "身体是今天最值得投资的资产，早睡早起，效率会给你惊喜。",
    "旧事重提不是为了翻篇，而是为了轻装；把结论写下来，心就定了。",
    "适合断舍离的一天，清理物品也是清理心绪，空间腾出来，机会才进得来。",
    "把大目标拆成三件小事，完成它们，今天就已经赢了。",
]


def fetch_offline(sign: Sign, date: dt.date) -> Fortune:
    """离线占卜引擎：同一（日期, 星座）结果恒定，可复现。"""
    seed = f"{date.isoformat()}|{sign.key}|zhangyu"
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16))

    scores = {key: rng.randint(3, 5) for key in ("all", "love", "work", "money", "health")}
    indices = {key: f"{min(97, score * 20 + rng.randint(-6, 12))}%" for key, score in scores.items()}

    others = [s for s in SIGNS if s.key != sign.key]
    return Fortune(
        sign=sign,
        date=date,
        source="章鱼AI 离线占卜引擎（兜底）",
        overall=rng.choice(_OVERALLS),
        short_comment=rng.choice(_COMMENTS),
        scores=scores,
        indices=indices,
        lucky_color=rng.choice(_COLORS),
        lucky_number=str(rng.randint(1, 99)),
        lucky_buddy=rng.choice(others).cn,
        yi=rng.choice(_YI),
        ji=rng.choice(_JI),
        offline=True,
    )


# --------------------------------------------------------------------------- #
# 统一入口：按序降级
# --------------------------------------------------------------------------- #

PROVIDERS = {
    "xxapi": fetch_xxapi,
    "freehoroscope": fetch_freehoroscope,
}

DEFAULT_PROVIDERS: List[str] = ["xxapi", "freehoroscope", "offline"]


def get_fortune(
    sign: Sign,
    date: dt.date,
    providers: Optional[Sequence[str]] = None,
    timeout: float = 15.0,
) -> Fortune:
    """按配置顺序获取运势；全部线上源失败时用离线引擎兜底。"""
    chain = list(providers or DEFAULT_PROVIDERS)
    if "offline" not in chain:
        chain.append("offline")  # 永远兜底

    errors: List[str] = []
    for name in chain:
        try:
            if name == "offline":
                fortune = fetch_offline(sign, date)
            else:
                fetcher = PROVIDERS.get(name)
                if fetcher is None:
                    raise ProviderError(f"未知数据源 {name!r}")
                fortune = fetcher(sign, date, timeout=timeout)
            if errors:
                fortune.source += "（经降级）"
            return fortune
        except ProviderError as exc:
            errors.append(f"{name}: {exc}")
            print(f"[zhangyu] 数据源 {name} 失败，尝试下一源… ({exc})", file=sys.stderr)

    raise ProviderError("所有数据源均失败: " + "; ".join(errors))
