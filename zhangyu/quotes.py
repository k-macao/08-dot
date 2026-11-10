"""指数行情数据源：东方财富 push2（主）→ 新浪 hq（备）→ 本地缓存（兜底）。

覆盖三组指数（标准版 10 项，见 INDEX_SPECS）：
    A股：上证指数 / 深证成指 / 创业板指 / 沪深300
    港股：恒生指数 / 恒生国企 / 恒生科技
    美股：道琼斯 / 纳斯达克（综合）/ 标普500

设计要点：
    1. 东财 ulist.np 一次批量取回全部 10 项（JSON，免密钥），三个镜像主机依次降级；
    2. 新浪 hq.sinajs 作为独立备用源（GBK 文本，需 Referer），三种市场字段布局不同；
    3. 一个失效 secid 会让东财整批返回 data:null —— 所以批量失败自动走新浪，
       两边都失败时读 data/quotes_cache.json 缓存（带时间标注，不编数据）；
    4. in_trading_window 判断当前是否处于 A股/港股（京/澳 09:30-17:00 工作日）
       或美股（美东 09:30-17:00 工作日）交易时段，供 --trading-only 门控使用。

secid 实测口径（参考 k-macao/001 的踩坑记录）：
    恒生科技 = 124.HSTECH（100.HSTECH 已失效，混入批量会拖垮整批）；
    东财 100.NDX 返回的是纳斯达克**综合**指数（与 ^IXIC 同值，非纳斯达克100）。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

USER_AGENT = "zhangyu-quotes/2.0 (+Dot Open Platform Text API)"

# --------------------------------------------------------------------------- #
# 指数清单
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IndexSpec:
    key: str        # 稳定内部键
    group: str      # 分组标题（卡片小节）
    label: str      # 卡片上显示的短名
    em: str         # 东方财富 secid
    sina: str       # 新浪 hq 代码


INDEX_SPECS: tuple[IndexSpec, ...] = (
    IndexSpec("sh", "A股", "上证", "1.000001", "sh000001"),
    IndexSpec("sz", "A股", "深成", "0.399001", "sz399001"),
    IndexSpec("cyb", "A股", "创业板", "0.399006", "sz399006"),
    IndexSpec("hs300", "A股", "沪深300", "1.000300", "sh000300"),
    IndexSpec("hsi", "港股", "恒指", "100.HSI", "hkHSI"),
    IndexSpec("hscei", "港股", "恒生国企", "100.HSCEI", "hkHSCEI"),
    IndexSpec("hstech", "港股", "恒生科技", "124.HSTECH", "hkHSTECH"),
    IndexSpec("dji", "美股", "道琼斯", "100.DJIA", "gb_$dji"),
    IndexSpec("ixic", "美股", "纳斯达克", "100.NDX", "gb_$ixic"),
    IndexSpec("spx", "美股", "标普500", "100.SPX", "gb_$inx"),
)

SPEC_BY_KEY: Dict[str, IndexSpec] = {s.key: s for s in INDEX_SPECS}
GROUP_ORDER: tuple[str, ...] = ("A股", "港股", "美股")

# 东财 f14 全名 → 内部键（仅用于 f12/f13 都缺失时的名称兜底；短标签子串匹配兜大部分）
EM_NAME_ALIASES: Dict[str, str] = {
    "上证指数": "sh",
    "深证成指": "sz",
    "创业板指": "cyb",
    "沪深300": "hs300",
    "恒生指数": "hsi",
    "恒生中国企业指数": "hscei",
    "恒生国企指数": "hscei",
    "恒生科技指数": "hstech",
    "道琼斯": "dji",
    "道琼斯工业平均指数": "dji",
    "纳斯达克综合指数": "ixic",
    "纳斯达克指数": "ixic",
    "标普500": "spx",
    "标普500指数": "spx",
}

# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


class QuoteError(RuntimeError):
    """行情源请求/解析失败（会触发向下一数据源降级）。"""


@dataclass
class Quote:
    """一条指数行情。pct 为涨跌幅百分数（如 0.56 表示 +0.56%），未知为 None。"""

    key: str
    group: str
    label: str
    price: float
    pct: Optional[float] = None


@dataclass
class QuoteSet:
    """一次取数结果：按 INDEX_SPECS 顺序排列的行情 + 来源标注。"""

    quotes: List[Quote]
    source: str
    cached: bool = False
    saved_at: Optional[str] = None  # 缓存写入/读取时刻（ISO），仅 cached 时有意义


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _http_get(url: str, headers: Optional[dict] = None, timeout: float = 12.0) -> bytes:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    request = Request(url, headers=hdrs)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise QuoteError(f"HTTP {exc.code} @ {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise QuoteError(f"网络错误 @ {url}: {exc}") from exc


def _http_get_json(url: str, headers: Optional[dict] = None, timeout: float = 12.0) -> dict:
    raw = _http_get(url, headers=headers, timeout=timeout)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise QuoteError(f"响应不是合法 JSON @ {url}") from exc
    if not isinstance(payload, dict):
        raise QuoteError(f"响应结构异常 @ {url}")
    return payload


# --------------------------------------------------------------------------- #
# 数据源 1：东方财富 push2 ulist.np（主用）
# --------------------------------------------------------------------------- #

EM_HOSTS = ("push2.eastmoney.com", "82.push2.eastmoney.com", "72.push2.eastmoney.com")
# f2 最新价 / f3 涨跌幅% / f4 涨跌额 / f12 代码 / f13 市场号 / f14 名称 / f124 行情时间戳
EM_FIELDS = "f2,f3,f4,f12,f13,f14,f124"
EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"


def _em_url(host: str) -> str:
    secids = ",".join(s.em for s in INDEX_SPECS)
    # secids 里的逗号/点号原样保留（urlencode 会把逗号转义，东财也认，但原样更直白）
    return (
        f"https://{host}/api/qt/ulist.np/get"
        f"?fltt=2&invt=2&ut={EM_UT}&secids={secids}&fields={EM_FIELDS}"
    )


def _num(value) -> Optional[float]:
    """东财 fltt=2 后多为数值，停牌/缺失时是 '-'；解析不了返回 None。"""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_eastmoney(payload: dict) -> Dict[str, Quote]:
    """解析 ulist.np 响应 → {key: Quote}；一行都解析不出则抛 QuoteError。

    行与 secid 的匹配按三级降级：
        1) f13.f12 重建 secid 精确匹配（如 124.HSTECH）；
        2) f12 与 secid 后缀匹配（f13 缺失时）；
        3) 名称关键词兜底。
    """
    diff = ((payload or {}).get("data") or {}).get("diff")
    if isinstance(diff, dict):  # 个别镜像会把 diff 返回成 dict
        diff = list(diff.values())
    if not isinstance(diff, list) or not diff:
        raise QuoteError("东财响应无 diff 数据（失效 secid 会让整批 data:null）")

    by_secid = {s.em: s for s in INDEX_SPECS}
    by_code = {s.em.split(".", 1)[1]: s for s in INDEX_SPECS}

    def _by_name(name: str) -> Optional[IndexSpec]:
        """f14 与短标签互相包含即可（东财名『上证指数』vs 标签『上证』）。"""
        if not name:
            return None
        alias = EM_NAME_ALIASES.get(name)
        if alias:
            return SPEC_BY_KEY[alias]
        for spec in INDEX_SPECS:
            if spec.label == name or spec.label in name or name in spec.label:
                return spec
        return None

    out: Dict[str, Quote] = {}
    for row in diff:
        if not isinstance(row, dict):
            continue
        price = _num(row.get("f2"))
        if price is None or price <= 0:
            continue
        f12 = str(row.get("f12") or "").strip()
        f13 = row.get("f13")
        spec = None
        if f13 not in (None, "") and f"{f13}.{f12}" in by_secid:
            spec = by_secid[f"{f13}.{f12}"]
        elif f12 in by_code:
            spec = by_code[f12]
        else:
            spec = _by_name(str(row.get("f14") or "").strip())
        if spec is None or spec.key in out:
            continue
        out[spec.key] = Quote(
            key=spec.key, group=spec.group, label=spec.label,
            price=price, pct=_num(row.get("f3")),
        )
    if not out:
        raise QuoteError("东财响应里没有任何可解析的指数行")
    return out


def fetch_eastmoney(timeout: float = 12.0) -> Dict[str, Quote]:
    """依次尝试东财三个镜像主机，返回全部能取到的行情。"""
    errors: List[str] = []
    for host in EM_HOSTS:
        try:
            payload = _http_get_json(_em_url(host), timeout=timeout)
            return parse_eastmoney(payload)
        except QuoteError as exc:
            errors.append(f"{host}: {exc}")
            print(f"[zhangyu] 东财 {host} 失败，尝试下一源… ({exc})", file=sys.stderr)
    raise QuoteError("东财全部主机失败: " + "; ".join(errors))


# --------------------------------------------------------------------------- #
# 数据源 2：新浪财经 hq.sinajs（备用，独立解析）
# --------------------------------------------------------------------------- #

SINA_URL = "https://hq.sinajs.cn/list=" + ",".join(s.sina for s in INDEX_SPECS)
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn/"}
_HQ_RE = re.compile(r'hq_str_([^=]+)="([^"]*)"')


def _sina_quote(spec: IndexSpec, parts: Sequence[str]) -> Optional[Quote]:
    """按市场前缀解析一行新浪行情；字段不足/价格非法返回 None。

    A股(sh/sz): [0]名称 [1]今开 [2]昨收 [3]最新 [4]最高 [5]低 … [30]日期 [31]时间
    港股(hk):   [0]英文名 [1]中文名 [2]今开 [3]昨收 [4]高 [5]低 [6]最新 [7]涨跌额 [8]涨跌幅
    美股(gb_):  [0]名称 [1]最新 [2]涨跌幅% [3]时间 [4]涨跌额 [5]今开 …
    """
    try:
        if spec.sina.startswith(("sh", "sz")):
            if len(parts) < 4:
                return None
            price, prev = float(parts[3]), float(parts[2])
            pct = (price / prev - 1) * 100 if prev else None
        elif spec.sina.startswith("hk"):
            if len(parts) < 7:
                return None
            price, prev = float(parts[6]), float(parts[3])
            pct = None
            if len(parts) > 8 and parts[8]:
                try:
                    pct = float(parts[8])
                except ValueError:
                    pct = None
            if pct is None and prev:
                pct = (price / prev - 1) * 100
        else:  # gb_ 美股
            if len(parts) < 2:
                return None
            price = float(parts[1])
            pct = None
            if len(parts) > 2 and parts[2]:
                try:
                    pct = float(parts[2])
                except ValueError:
                    pct = None
    except (ValueError, ZeroDivisionError):
        return None
    if price <= 0:
        return None
    return Quote(key=spec.key, group=spec.group, label=spec.label, price=price, pct=pct)


def parse_sina(text: str) -> Dict[str, Quote]:
    """解析 hq.sinajs 响应（GBK 文本）→ {key: Quote}；一行都解析不出则抛。"""
    by_code = {s.sina: s for s in INDEX_SPECS}
    out: Dict[str, Quote] = {}
    for match in _HQ_RE.finditer(str(text or "")):
        code, payload = match.group(1).strip(), match.group(2)
        spec = by_code.get(code)
        if not spec or not payload or spec.key in out:
            continue  # 空串 = 该代码无行情，跳过
        quote = _sina_quote(spec, payload.split(","))
        if quote:
            out[spec.key] = quote
    if not out:
        raise QuoteError("新浪响应里没有任何可解析的指数行")
    return out


def fetch_sina(timeout: float = 12.0) -> Dict[str, Quote]:
    raw = _http_get(SINA_URL, headers=SINA_HEADERS, timeout=timeout)
    return parse_sina(raw.decode("gbk", errors="replace"))


# --------------------------------------------------------------------------- #
# 本地缓存（两边都挂时兜底：展示上一次成功数据并标注，不编数据）
# --------------------------------------------------------------------------- #


def save_cache(path: Path, quoteset: QuoteSet, now: Optional[dt.datetime] = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        saved_at = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
        path.write_text(
            json.dumps(
                {
                    "saved_at": saved_at,
                    "source": quoteset.source,
                    "quotes": [
                        {"key": q.key, "price": q.price, "pct": q.pct}
                        for q in quoteset.quotes
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:  # 缓存写失败不影响本次取数
        print(f"[zhangyu] 警告：行情缓存写入失败 ({exc})", file=sys.stderr)


def load_cache(path: Path) -> Optional[QuoteSet]:
    """读取缓存并重建 QuoteSet；缺失/损坏/键对不上返回 None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    quotes: List[Quote] = []
    for row in data.get("quotes") or []:
        spec = SPEC_BY_KEY.get(str(row.get("key") or ""))
        price = _num(row.get("price"))
        if spec is None or price is None or price <= 0:
            continue
        quotes.append(Quote(
            key=spec.key, group=spec.group, label=spec.label,
            price=price, pct=_num(row.get("pct")),
        ))
    if not quotes:
        return None
    saved_at = str(data.get("saved_at") or "")
    stamp = ""
    if saved_at:
        try:
            stamp = dt.datetime.fromisoformat(saved_at).strftime("%m-%d %H:%M")
        except ValueError:
            stamp = saved_at[:16]
    source = f"缓存({stamp})" if stamp else "缓存"
    return QuoteSet(quotes=quotes, source=source, cached=True, saved_at=saved_at or None)


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #

_FETCHER_NAMES = ("em", "sina")
_SOURCE_LABELS = {"em": "东方财富", "sina": "新浪财经"}


def _fetcher(name: str):
    """按名取 fetcher（调用时解析，测试可 monkeypatch fetch_eastmoney/fetch_sina）。"""
    if name == "em":
        return fetch_eastmoney
    if name == "sina":
        return fetch_sina
    raise QuoteError(f"未知数据源 {name!r}（可选 em / sina / auto）")


def get_quotes(
    *,
    source: str = "auto",
    cache_path: Optional[Path] = None,
    timeout: float = 12.0,
    now: Optional[dt.datetime] = None,
) -> QuoteSet:
    """按 source 取行情；auto = 东财 → 新浪 → 本地缓存。

    两边都只取到部分指数时合并结果（宁可少显示几项也不整卡缺失）；
    一项都没取到且无缓存时抛 QuoteError。
    """
    order = ["em", "sina"] if source in ("", "auto", None) else [source]
    if source not in ("", "auto", None) and source not in _FETCHER_NAMES:
        raise QuoteError(f"未知数据源 {source!r}（可选 em / sina / auto）")

    merged: Dict[str, Quote] = {}
    used: List[str] = []
    errors: List[str] = []
    for name in order:
        try:
            got = _fetcher(name)(timeout=timeout)
        except QuoteError as exc:
            errors.append(f"{name}: {exc}")
            continue
        merged.update(got)
        used.append(_SOURCE_LABELS[name])
        if len(merged) >= len(INDEX_SPECS):
            break

    if merged:
        quotes = [merged[s.key] for s in INDEX_SPECS if s.key in merged]
        quoteset = QuoteSet(quotes=quotes, source=" + ".join(used))
        if len(quotes) == len(INDEX_SPECS) and cache_path is not None:
            save_cache(cache_path, quoteset, now=now)
        return quoteset

    if cache_path is not None:
        cached = load_cache(Path(cache_path))
        if cached is not None:
            print(f"[zhangyu] 行情源全部失败，改用本地缓存 {cached.source}", file=sys.stderr)
            return cached
    raise QuoteError("所有行情源均失败: " + "; ".join(errors))


def load_fixture(path: str) -> QuoteSet:
    """离线夹具：接受「东财原始响应」或「归一化 JSON」两种格式。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuoteError(f"夹具读取失败 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise QuoteError(f"夹具结构异常 {path}")

    if "data" in data:  # 东财 ulist 原始响应
        quotes = list(parse_eastmoney(data).values())
        source = "fixture(东财)"
    else:  # 归一化：{"quotes": [{"key","price","pct"}, …]}
        quotes = []
        for row in data.get("quotes") or []:
            spec = SPEC_BY_KEY.get(str(row.get("key") or ""))
            price = _num(row.get("price"))
            if spec is None or price is None or price <= 0:
                raise QuoteError(f"夹具指数键非法: {row.get('key')!r}")
            quotes.append(Quote(
                key=spec.key, group=spec.group, label=spec.label,
                price=price, pct=_num(row.get("pct")),
            ))
        if not quotes:
            raise QuoteError(f"夹具无行情数据 {path}")
        source = str(data.get("source") or "fixture")

    # 按清单顺序排列（夹具里顺序随意）
    order = {s.key: i for i, s in enumerate(INDEX_SPECS)}
    quotes.sort(key=lambda q: order.get(q.key, 999))
    return QuoteSet(quotes=quotes, source=source)


# --------------------------------------------------------------------------- #
# 交易时段门控（--trading-only）
# --------------------------------------------------------------------------- #


def in_trading_window(now_utc: dt.datetime) -> Optional[str]:
    """now_utc（aware）处于交易时段则返回时段名，否则 None。

    A股/港股：京/澳时间（UTC+8）工作日 09:30–17:00
    美股：美东时间工作日 09:30–17:00（含夏令时；缺 tzdata 时跳过该窗口）
    端点取 17:00 前，容忍 GitHub cron 最多半小时的延迟。
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)

    try:
        cn = now_utc.astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:  # 缺 tzdata 时退固定 UTC+8
        cn = now_utc.astimezone(dt.timezone(dt.timedelta(hours=8)))
    if cn.weekday() < 5 and dt.time(9, 30) <= cn.time() < dt.time(17, 0):
        return "A股/港股"

    try:
        ny = now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None  # 缺 tzdata 时无法可靠判断美股时段（夏令时漂移），宁可不推
    if ny.weekday() < 5 and dt.time(9, 30) <= ny.time() < dt.time(17, 0):
        return "美股"
    return None
