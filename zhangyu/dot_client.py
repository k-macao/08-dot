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
) -> str:
    """推送文本卡片到设备，返回接口 message；失败抛出 DotAPIError。"""
    device_id = (device_id or "").strip()
    if not device_id:
        raise DotAPIError(None, "设备序列号 (device_id) 为空")
    token = normalize_api_key(api_key)

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
