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


def normalize_api_key(api_key: str) -> str:
    """确保 key 带 `dot_app_` 前缀（文档示例为 Bearer dot_app_xxx）。"""
    key = (api_key or "").strip()
    if not key:
        raise DotAPIError(None, "API Key 为空，请在 Dot. App 中创建并导出 API Key")
    if not key.startswith(API_KEY_PREFIX):
        key = API_KEY_PREFIX + key
    return key


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
