"""Dot 开放平台 Text API 客户端（纯标准库实现）。

接口文档: https://dot.mindreset.tech/docs/service/open/text_api
    POST {base}/api/authV2/open/device/{deviceId}/text
    Header:  Authorization: Bearer dot_app_{apiKey}
    Body:    taskType / refreshNow / title / message / signature / icon / link
             / taskKey / taskAlias / styles
    限频:    10 requests/s
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DOT_BASE_URL = "https://dot.mindreset.tech"
API_KEY_PREFIX = "dot_app_"


class DotAPIError(RuntimeError):
    """Dot 云接口返回错误。"""

    def __init__(self, status: Optional[int], message: str):
        super().__init__(f"Dot API 错误 (HTTP {status}): {message}")
        self.status = status
        self.message = message


# 从文档/邮件/聊天窗口复制密钥时最常见的一圈"污染"
API_KEY_QUOTES = "\"'\u201c\u201d\u2018\u2019`"


def clean_api_key(raw: str) -> "tuple[str, list[str]]":
    """清掉粘贴污染，返回 (key, 清理说明列表)。

    Dot 云对 key 是逐字符比对的，多一个引号、空格或 `Bearer ` 前缀都会得到
    `HTTP 400 This API key is invalid`。这里把常见污染就地修掉，并把"修了什么"
    记下来供日志/自检展示（绝不回显密钥本体）。

    引号可能在 `Bearer ` 的外面也可能在里面（`"Bearer key"` / `Bearer "key"`），
    所以剥离动作跑两轮，直到稳定。
    """
    notes: list = []
    key = raw or ""

    def note(item: str) -> None:
        if item not in notes:
            notes.append(item)

    for _ in range(2):
        before = key
        if key != key.strip():
            note("首尾空白")
        key = key.strip()

        unquoted = key.strip(API_KEY_QUOTES)
        if unquoted != key:
            note("引号")
        key = unquoted

        if len(key) >= 2 and key.startswith("<") and key.endswith(">"):
            note("尖括号")
            key = key[1:-1]

        if key.lower().startswith("bearer"):
            note("Bearer 前缀")
            key = key[len("bearer"):].lstrip(" :：")

        if key == before:
            break

    if any(ch.isspace() for ch in key):
        note("中间空白")
        key = "".join(key.split())

    return key, notes


# 官方 Text API 文档里的真实 Key 形态（2026 版）：
#   Authorization: Bearer dot_app_<64 位>
# 因此合法 token 长度 = 8 + 64 = 72。长度只能当"软校验"用（密钥格式将来可能变），
# 但能一眼识破"填进去的根本不是 Dot Key"。
API_KEY_EXPECTED_BODY_LEN = 64

# 其它服务的 token 前缀：填错来源时能一眼认出（不是 Dot 的 Key）
FOREIGN_TOKEN_PREFIXES = (
    "ghp_", "gho_", "ghu_", "ghs_", "github_pat_", "sk-", "sk_", "xoxb-",
    "xoxp-", "secret_", "eyJ",  # JWT
)


def api_key_health(raw: str) -> "dict":
    """把 Key 的形状讲清楚：是否带前缀、payload 多长、是否与官方格式吻合。"""
    key, notes = clean_api_key(raw)
    has_prefix = key.startswith(API_KEY_PREFIX)
    body = key[len(API_KEY_PREFIX):] if has_prefix else key
    expected_total = len(API_KEY_PREFIX) + API_KEY_EXPECTED_BODY_LEN
    problems: list = []

    if not key:
        problems.append("为空")
    else:
        if not has_prefix:
            problems.append(
                f"没有 {API_KEY_PREFIX} 前缀（官方 token 形如 "
                f"{API_KEY_PREFIX}<64 位>，共 {expected_total} 字符；"
                f"你的值 {len(key)} 字符，补前缀后 {len(key) + len(API_KEY_PREFIX)} 字符）"
            )
        if body and len(body) != API_KEY_EXPECTED_BODY_LEN:
            delta = len(body) - API_KEY_EXPECTED_BODY_LEN
            problems.append(
                f"payload {len(body)} 位，官方示例为 {API_KEY_EXPECTED_BODY_LEN} 位"
                f"（{'+' if delta > 0 else ''}{delta}）"
            )
        for prefix in FOREIGN_TOKEN_PREFIXES:
            if body.startswith(prefix):
                problems.append(
                    f"像其它服务的 token（以 {prefix} 开头），不是 Dot API Key"
                )
                break
        if any(not (ch.isalnum() or ch in "_-") for ch in body):
            problems.append("含非字母数字字符（也可能是别的服务的 token）")

    return {
        "cleaned": key,
        "notes": notes,
        "has_prefix": has_prefix,
        "body_len": len(body),
        "total_len": len(key),
        "expected_total_len": expected_total,
        "problems": problems,
        "looks_valid": not problems,
    }


def api_key_candidates(raw: str) -> "list[str]":
    """按可能性给出要发送的 Authorization 值，供 400 时自动换形重试。

    官方要求 `Bearer dot_app_<key>`：
      1. 规范形式（缺前缀时补上）；
      2. 原值（有些面板直接给出完整 token，或新版格式自带前缀规则）。
    两种都失败才判定 Key 本身无效。
    """
    cleaned, _ = clean_api_key(raw)
    if not cleaned:
        return []
    normal = normalize_api_key(cleaned)
    return [normal] if normal == cleaned else [normal, cleaned]


def normalize_api_key(api_key: str) -> str:
    """清洗粘贴污染，并确保 key 带 `dot_app_` 前缀（Bearer dot_app_xxx）。"""
    key, _notes = clean_api_key(api_key)
    if not key:
        raise DotAPIError(None, "API Key 为空，请在 Dot. App 中创建并导出 API Key")
    if not key.startswith(API_KEY_PREFIX):
        key = API_KEY_PREFIX + key
    return key


def api_key_summary(raw: str) -> str:
    """给日志/自检用的安全指纹：只有长度与结构特征，没有密钥内容。"""
    key, notes = clean_api_key(raw)
    if not key:
        return "空"
    parts = [f"长度 {len(key)}"]
    parts.append("带 dot_app_ 前缀" if key.startswith(API_KEY_PREFIX)
                 else "无 dot_app_ 前缀（发送时会自动补）")
    if notes:
        parts.append("已清理 " + "/".join(notes))
    if len(key) < 24:
        parts.append("偏短，可能不完整 ⚠️")
    return "，".join(parts)


def looks_like_api_key(value: str) -> bool:
    """判断某个值是不是"密钥"而不是设备序列号（用于发现两个 Secret 填反）。"""
    key, _ = clean_api_key(value)
    return key.startswith("dot_app_") or key.startswith("dot_") or len(key) > 40


def build_body(
    card: Dict[str, str],
    *,
    task_type: str = "loop",
    task_key: Optional[str] = None,
    task_alias: Optional[str] = None,
    refresh_now: bool = True,
    icon: Optional[str] = None,
    link: Optional[str] = None,
    styles: Optional[dict] = None,
) -> Dict[str, Any]:
    """组装 Text API 请求体；None 字段不发送。"""
    if task_type not in ("loop", "fixed"):
        raise DotAPIError(None, f"taskType 仅支持 loop/fixed，收到 {task_type!r}")

    body: Dict[str, Any] = {
        "taskType": task_type,
        "refreshNow": bool(refresh_now),
    }
    if card.get("title"):
        body["title"] = card["title"]
    if card.get("message"):
        body["message"] = card["message"]
    if card.get("signature"):
        body["signature"] = card["signature"]
    if task_key:
        body["taskKey"] = task_key
    if task_alias is not None:
        body["taskAlias"] = task_alias
    if icon:
        body["icon"] = icon
    if link:
        body["link"] = link
    if styles:
        body["styles"] = styles
    return body


def push_text(
    body: Dict[str, Any],
    *,
    device_id: str,
    api_key: str,
    base_url: str = DOT_BASE_URL,
    timeout: float = 20.0,
    retries: int = 3,
    note: Optional[list] = None,
) -> str:
    """推送文本卡片到设备，返回接口 message；失败抛出 DotAPIError。

    Key 的两种形态（补前缀 / 原值）会依次尝试：Dot 云对 token 逐字符比对，
    某些面板导出的值已自带前缀规则，自动换形可以避免"其实只差个前缀"的苦战。
    换形重试的结果会追加到 note（若提供），以便调用方如实报告。
    """
    device_id = (device_id or "").strip()
    if not device_id:
        raise DotAPIError(None, "设备序列号 (device_id) 为空")

    candidates = api_key_candidates(api_key)
    if not candidates:
        raise DotAPIError(None, "API Key 为空，请在 Dot. App 中创建并导出 API Key")

    last_exc: Optional[DotAPIError] = None
    for index, token in enumerate(candidates):
        try:
            result = _push_text_with_token(
                body, device_id=device_id, token=token, base_url=base_url,
                timeout=timeout, retries=retries,
            )
        except DotAPIError as exc:
            if exc.status in (400, 401) and index + 1 < len(candidates):
                last_exc = exc
                if note is not None:
                    note.append(
                        f"以 {'补前缀' if index == 0 else '原值'} 形式被拒"
                        f"（HTTP {exc.status}），改用另一种形态重试"
                    )
                continue
            raise
        if index > 0 and note is not None:
            note.append("服务端接受的是「原值」形态（未补 dot_app_ 前缀）")
        return result

    assert last_exc is not None
    raise last_exc


def _push_text_with_token(
    body: Dict[str, Any],
    *,
    device_id: str,
    token: str,
    base_url: str,
    timeout: float,
    retries: int,
) -> str:
    url = f"{base_url.rstrip('/')}/api/authV2/open/device/{device_id}/text"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "zhangyu-daily-divination/1.0",
        },
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw) if raw.strip() else {}
                return str(payload.get("message") or "OK")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                message = json.loads(raw).get("message") or raw or exc.reason
            except (json.JSONDecodeError, AttributeError):
                message = raw or str(exc.reason)
            if 500 <= exc.code < 600 and attempt < retries:
                # 设备侧 5xx 可能是瞬时故障，稍后重试
                last_error = DotAPIError(exc.code, str(message))
                time.sleep(1.5 * attempt)
                continue
            raise DotAPIError(exc.code, str(message)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
                continue
            raise DotAPIError(None, f"网络错误: {exc}") from exc

    raise DotAPIError(None, f"重试 {retries} 次后仍失败: {last_error}")


def get_fixed_list(
    *, device_id: str, api_key: str, base_url: str = DOT_BASE_URL, timeout: float = 20.0
) -> Any:
    """查询设备“固定列表”内容，取其中 TEXT_API 项的 key 用于 taskType=fixed。"""
    device_id = (device_id or "").strip()
    if not device_id:
        raise DotAPIError(None, "设备序列号 (device_id) 为空")
    token = normalize_api_key(api_key)
    url = f"{base_url.rstrip('/')}/api/authV2/open/device/{device_id}/fixed/list"
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": "zhangyu-daily-divination/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise DotAPIError(exc.code, raw or str(exc.reason)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DotAPIError(None, f"网络错误: {exc}") from exc
