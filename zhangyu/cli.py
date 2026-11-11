"""章鱼AI.行情速报 — 命令行入口。

用法：
    python run_daily.py push      [--trading-only]   # 取最新指数行情并推送设备
                                  [--dry-run] [--show-payload] [--force]
                                  [--source auto|em|sina] [--fixture path.json]
    python run_daily.py preview   [--fixture path.json] [--source em|sina]
    python run_daily.py indices                        # 查看卡片上的指数清单
    python run_daily.py doctor    [--require-credentials]   # CI 预检用

    没有凭据也能自检：push --dry-run --show-payload 打印排版与请求体后正常退出。
    定时任务（GitHub Actions 交易时段 cron）带 --trading-only：
    A股/港股（京/澳 09:30–17:30 工作日）或美股（美东 09:30–17:30 工作日）
    之外的触发直接跳过（退出码 0），不会打到 Dot 云。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from . import APP_NAME, __version__
from .dot_client import (
    DotAPIError,
    api_key_candidates,
    api_key_health,
    api_key_summary,
    build_body,
    get_fixed_list,
    get_task_list,
    looks_like_api_key,
    normalize_api_key,
    pick_text_task,
    push_text,
    text_tasks,
)
from .formatter import CARD_ROWS, build_card
from .quotes import (
    INDEX_SPECS,
    QuoteError,
    QuoteSet,
    get_quotes,
    in_trading_window,
    load_fixture,
)

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

DEFAULTS: Dict[str, Any] = {
    "device_id": "",
    "title": APP_NAME,
    "brand": "章鱼AI",
    "source": "auto",  # auto | em（东方财富）| sina（新浪财经）
    "fixture": None,   # 离线夹具路径（东财原始响应或归一化 JSON）
    "task_type": "loop",  # loop | fixed
    "task_key": None,  # fixed 列表项的 key（可用 doctor 查询）
    "task_alias": APP_NAME,
    "refresh_now": True,
    "icon_url": None,
    "nfc_link": None,
    "timezone": "Asia/Macau",
    "state_file": "data/state.json",
    "cache_file": "data/quotes_cache.json",
}

# 幂等窗口：50 分钟内已推送过就跳过（对应「每小时最新」的整点节奏）
IDEMPOTENCY_MINUTES = 50


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    merged.update({k: v for k, v in override.items() if v is not None})
    return merged


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """默认值 <- 配置文件 <- 环境变量。"""
    cfg = dict(DEFAULTS)
    config_path = Path(path or "config.json")
    if path:
        if not config_path.exists():
            raise SystemExit(f"配置文件不存在: {config_path}")
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"配置文件不是合法 JSON: {config_path} ({exc})") from exc
        cfg = _deep_merge(cfg, file_cfg)

    # 环境变量覆盖（密钥类只走环境变量）
    if os.environ.get("DOT_DEVICE_ID"):
        cfg["device_id"] = os.environ["DOT_DEVICE_ID"].strip()
    if os.environ.get("ZHANGYU_TITLE"):
        cfg["title"] = os.environ["ZHANGYU_TITLE"].strip()
    if os.environ.get("ZHANGYU_SOURCE"):
        cfg["source"] = os.environ["ZHANGYU_SOURCE"].strip()
    return cfg


def local_now(tz_name: str) -> dt.datetime:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(minutes=480))  # 兜底 UTC+8
        print(f"[zhangyu] 未知时区 {tz_name!r}，使用 UTC+8", file=sys.stderr)
    return dt.datetime.now(tz)


def load_quotes(cfg: Dict[str, Any], args) -> QuoteSet:
    """取行情：夹具（测试/离线）优先，否则按 source 走线上链路。"""
    fixture = getattr(args, "fixture", None) or cfg.get("fixture")
    if fixture:
        return load_fixture(str(fixture))
    source = (getattr(args, "source", None) or cfg.get("source") or "auto").strip()
    return get_quotes(
        source=source,
        cache_path=Path(cfg.get("cache_file") or "data/quotes_cache.json"),
        timeout=15.0,
    )


def _print_card(card: Dict[str, Any], quoteset: QuoteSet) -> None:
    print("=" * 46)
    if card.get("title"):
        print(f"标题：{card['title']}")
    print("-" * 46)
    print(card["message"])
    print("-" * 46)
    if card.get("signature"):
        print(f"签名：{card['signature']}")
    print(f"数据源：{quoteset.source}（取数 {len(quoteset.quotes)} 项）")
    print("=" * 46)


# --------------------------------------------------------------------------- #
# 凭据
# --------------------------------------------------------------------------- #

# config.example.json 里的占位值：原样照抄时必须当成"没配置"，否则会打到
# Dot 云再吃一个看不懂的 404。
PLACEHOLDER_DEVICE_IDS = {"", "YOUR_DEVICE_SERIAL_NUMBER", "YOUR_DEVICE_ID", "xxx"}

CREDENTIAL_HINT = """如何配置：
  · 本地运行：cp config.example.json config.json 后填 "device_id"，
    或  export DOT_DEVICE_ID='设备序列号'   export DOT_API_KEY='dot_app_xxx'
  · GitHub Actions：仓库 Settings → Secrets and variables → Actions
      Secrets：    DOT_API_KEY = dot_app_xxx
      Secrets 或 Variables：DOT_DEVICE_ID = 设备序列号（两处都支持）
    改完后重跑 workflow（Actions → 行情速报推送 → Run workflow）。
  · 只想看请求体/排版而不推送：python run_daily.py push --dry-run --show-payload"""


def read_credentials(cfg: Dict[str, Any]) -> tuple:
    """返回 (device_id, api_key)，均为去掉首尾空白后的字符串。"""
    device_id = str(cfg.get("device_id") or "").strip()
    api_key = os.environ.get("DOT_API_KEY", "").strip()
    return device_id, api_key


def missing_credentials(device_id: str, api_key: str) -> list:
    """返回缺失项的名称列表（空列表 = 齐全）。"""
    problems = []
    if device_id in PLACEHOLDER_DEVICE_IDS:
        problems.append("device_id")
    if not api_key:
        problems.append("DOT_API_KEY")
    return problems


def swapped_secrets(device_id: str, api_key: str) -> bool:
    """两个 Secret 填反的典型症状：device_id 长得像密钥。"""
    return bool(device_id) and looks_like_api_key(device_id)


def api_key_shape_lines(api_key: str) -> list:
    """Key 形状的**硬问题**（不回显密钥内容）。"""
    return list(api_key_health(api_key)["problems"])


def api_key_shape_hints(api_key: str) -> list:
    """与文档示例的差异（可能仍然可用，仅作提示）。"""
    return list(api_key_health(api_key)["hints"])


def warn_api_key_shape(api_key: str) -> None:
    """只在"一定不对"时告警（空 / 别的服务的 token / 含非法字符）。

    与文档示例不同的位数或缺少 `dot_app_` 前缀**不再**当错误喊 ——
    实测存在无前缀但完全可用的 Key，喊错会把人带偏。
    """
    problems = api_key_shape_lines(api_key)
    if not problems:
        return
    print("⚠️ DOT_API_KEY 看起来不是 Dot API Key（仍会照发）：", file=sys.stderr)
    for item in problems:
        print(f"  · {item}", file=sys.stderr)
    print("  请在 Dot. App → API Key 里重新导出完整值。", file=sys.stderr)


def api_key_rejected_hint(api_key: str, device_id: str) -> None:
    """HTTP 400/401「This API key is invalid」的定向排查清单。"""
    print("提示：Dot 云明确判定 API Key 无效（不是权限问题，也不是设备问题）。",
          file=sys.stderr)
    print(f"  实际发送的 Key：{api_key_summary(api_key)}", file=sys.stderr)
    for item in api_key_shape_lines(api_key):
        print(f"  ⚠️ {item}", file=sys.stderr)
    if swapped_secrets(device_id, api_key):
        print(
            "  ⚠️ 你的 DOT_DEVICE_ID 看起来是 API Key —— 两个 Secret 很可能填反了。",
            file=sys.stderr,
        )
    print("  逐项核对：", file=sys.stderr)
    print("   1. 值是否 = Dot. App → 设置/开发者 → 导出的完整 Key（dot_app_…），"
          "复制时别带引号、空格、`Bearer `；本工具会自动清常见污染但仍建议原样粘贴",
          file=sys.stderr)
    print("   2. Key 是否已失效：在 App 里重新生成/导出后，把新值写进 Secret"
          "（重新生成通常会让旧 Key 立即作废）", file=sys.stderr)
    print("   3. 是否填错了位置：`DOT_API_KEY` 必须是 Secret（Variables 页不会读到），"
          "`DOT_DEVICE_ID` = 设备序列号", file=sys.stderr)
    print("   4. Key 与设备是否同一账号（换过账号/换绑设备需重新导出）", file=sys.stderr)
    print("   5. 本地快速验证：python run_daily.py doctor --check-key", file=sys.stderr)


def state_key_form(state_path: Path) -> Optional[str]:
    """读取上一次被服务端接受的 Key 形态（"raw" / "prefixed"）。"""
    env = os.environ.get("ZHANGYU_KEY_FORM", "").strip().lower()
    if env in ("raw", "prefixed"):
        return env
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    form = str(state.get("key_form") or "").lower()
    return form if form in ("raw", "prefixed") else None


def describe_task_lists(device_id: str, api_key: str, timeout: float) -> "dict":
    """查 loop / fixed 两个列表，找出 Text API 槽位。"""
    result: Dict[str, Any] = {"loop": None, "fixed": None, "errors": {}}
    for task_type in ("loop", "fixed"):
        try:
            tasks = get_task_list(
                task_type, device_id=device_id, api_key=api_key, timeout=timeout
            )
            result[task_type] = tasks
        except DotAPIError as exc:
            result["errors"][task_type] = exc
    return result


def report_task_lists(lists: Dict[str, Any]) -> None:
    for task_type in ("loop", "fixed"):
        if task_type in lists["errors"]:
            print(f"- {task_type} 列表: 查询失败（{lists['errors'][task_type].message}）")
            continue
        tasks = lists[task_type] or []
        picked = pick_text_task(tasks)
        summary = ", ".join(
            f"{str(t.get('type', '?'))}"
            + (f"({t.get('key')})" if t.get("key") else "")
            for t in tasks
            if isinstance(t, dict)
        ) or "空"
        if picked is None:
            print(f"- {task_type} 列表: {summary} ❌ 没有 Text API 内容")
        else:
            print(f"- {task_type} 列表: {summary} ✅ 有 Text API 内容")


def content_missing_hint(task_type: str, key: str) -> None:
    """Text API 内容没加进列表时的精确操作步骤。"""
    print("怎么修（一次就好，之后无需再动）：", file=sys.stderr)
    print("  Dot. App → 内容工坊 / Content Studio → 选择这台设备 →",
          file=sys.stderr)
    print(f"  把「Text API」内容添加到【{'循环' if task_type == 'loop' else '固定'}列表】"
          "（Add Text API to the loop list）→ 保存 → 重跑。", file=sys.stderr)


def resolve_content_target(
    cfg: Dict[str, Any], args, device_id: str, api_key: str
) -> "tuple[str, Optional[str]]":
    """决定这次写哪个列表的哪个槽位；能在 404 之前就把问题说清楚。

    返回 (task_type, task_key)。若循环列表里没有 Text API 内容而固定列表里有，
    自动改用固定列表（并在日志里说明），省掉一次必然失败的 404。
    """
    task_type = (args.task_type or cfg.get("task_type") or "loop").strip()
    task_key = args.task_key or cfg.get("task_key")
    explicit = bool(args.task_type or args.task_key)

    try:
        lists = describe_task_lists(device_id, api_key, float(getattr(args, "timeout", 20.0)))
    except Exception:  # 预检失败不阻断推送
        return task_type, task_key

    loop_pick = pick_text_task(lists["loop"]) if lists["loop"] is not None else None
    fixed_pick = pick_text_task(lists["fixed"]) if lists["fixed"] is not None else None

    if task_key:  # 显式指定就不猜
        return task_type, task_key

    current = loop_pick if task_type == "loop" else fixed_pick
    other_type = "fixed" if task_type == "loop" else "loop"
    other = fixed_pick if task_type == "loop" else loop_pick

    if current is not None:
        return task_type, (current.get("key") or None)

    if other is not None and not explicit:
        print(
            f"[zhangyu] 提醒：{'循环' if task_type == 'loop' else '固定'}列表里没有 Text API 内容，"
            f"但『{'固定' if other_type == 'loop' else '循环'}列表』有 —— 已自动改用该列表推送。",
            file=sys.stderr,
        )
        print(
            f"[zhangyu] 想固定下来：python run_daily.py push --task-type {other_type}"
            + (f" --task-key {other.get('key')}" if other.get("key") else ""),
            file=sys.stderr,
        )
        return other_type, (other.get("key") or None)

    return task_type, task_key


def print_payload(body: Dict[str, Any], device_id: str) -> None:
    print("将要发送的请求体 (POST /api/authV2/open/device/{deviceId}/text):")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(f"deviceId: {device_id or '(未配置)'}")


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_push(args) -> int:
    cfg = load_config(args.config)
    now = local_now(cfg["timezone"])

    # 交易时段门控（定时任务专用）：休市时直接跳过，不取行情、不打 Dot 云。
    # --dry-run 是本地预览，不受门控影响。
    if getattr(args, "trading_only", False) and not args.dry_run:
        window = in_trading_window(now.astimezone(dt.timezone.utc))
        if not window:
            print("[zhangyu] 非交易时段（A股/港股/美股均休市），跳过本次推送。")
            return 0

    try:
        quoteset = load_quotes(cfg, args)
    except QuoteError as exc:
        print(f"行情获取失败：{exc}", file=sys.stderr)
        return 1

    title = "" if args.no_title else cfg["title"]
    card = build_card(
        quoteset,
        title=title,
        moment=now,
    )

    device_id, api_key = read_credentials(cfg)
    problems = missing_credentials(device_id, api_key)

    # 先确定写哪个列表的哪个槽位（能在必然 404 之前就把问题定位出来），
    # 再据此组装请求体。
    if problems:
        task_type = args.task_type or cfg["task_type"]
        task_key = args.task_key or cfg.get("task_key")
    else:
        task_type, task_key = resolve_content_target(cfg, args, device_id, api_key)

    body = build_body(
        card,
        task_type=task_type,
        task_key=task_key,
        task_alias=cfg.get("task_alias"),
        refresh_now=cfg.get("refresh_now", True),
        icon=cfg.get("icon_url"),
        link=cfg.get("nfc_link"),
    )

    # --show-payload 是"看请求体"的调试开关，必须在凭据校验之前打印 ——
    # 之前的实现先校验凭据，导致没配 device_id 时连请求体都看不到。
    if args.show_payload:
        print_payload(body, device_id)

    if args.dry_run:
        _print_card(card, quoteset)
        if problems:
            print(f"[dry-run] 缺 {', '.join(problems)}；仅本地排版/请求体预览，未推送。")
        else:
            print("[dry-run] 未调用 Dot 云接口。")
        return 0

    # 幂等：IDEMPOTENCY_MINUTES 分钟内已推送过则跳过（每小时一更的节奏）
    state_path = Path(cfg["state_file"])
    if state_path.exists() and not args.force:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
        last_at = state.get("last_push_at")
        if last_at:
            try:
                last_dt = dt.datetime.fromisoformat(str(last_at))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=now.tzinfo)
                age = now - last_dt.astimezone(now.tzinfo)
                if age < dt.timedelta(minutes=IDEMPOTENCY_MINUTES):
                    minutes = int(age.total_seconds() // 60)
                    print(
                        f"[zhangyu] {minutes} 分钟内已推送过，跳过；"
                        f"如需重推加 --force"
                    )
                    return 0
            except ValueError:
                pass  # 旧格式/坏值不阻塞

    if problems:
        if args.show_payload:
            print("[zhangyu] 以上仅为请求体预览，凭据不全，未发起推送。", file=sys.stderr)
        print(f"错误：未配置 {', '.join(problems)}，无法推送到设备。", file=sys.stderr)
        print(CREDENTIAL_HINT, file=sys.stderr)
        return 2

    warn_api_key_shape(api_key)
    form_notes: list = []
    key_info: dict = {}
    try:
        message = push_text(
            body,
            device_id=device_id,
            api_key=api_key,
            timeout=float(args.timeout),
            note=form_notes,
            preferred_form=state_key_form(state_path),
            info=key_info,
        )
    except DotAPIError as exc:
        for line in form_notes:
            print(f"[zhangyu] {line}", file=sys.stderr)
        print(f"推送失败：{exc}", file=sys.stderr)
        if exc.status in (400, 401):
            api_key_rejected_hint(api_key, device_id)
        if exc.status == 404:
            print("提示：404 表示这台设备的该列表里没有 Text API 内容槽位。",
                  file=sys.stderr)
            lists = describe_task_lists(device_id, api_key, float(args.timeout))
            if lists["errors"]:
                print("（顺带查了一下任务列表，结果如下）", file=sys.stderr)
            report_task_lists(lists)
            has_any = pick_text_task(lists["loop"] or []) or pick_text_task(
                lists["fixed"] or []
            )
            if has_any is None:
                content_missing_hint(task_type, task_key or "")
        if exc.status == 403:
            print("提示：403 表示该 API Key 无权操作此设备。", file=sys.stderr)
        return 1

    # 记录状态
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "last_push_at": now.isoformat(timespec="seconds"),
                    "source": quoteset.source,
                    "title": card["title"],
                    "message": card["message"],
                    "device_message": message,
                    "task_type": task_type,
                    "task_key": task_key,
                    **({"key_form": key_info["form"]} if key_info.get("form") else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[zhangyu] 警告：状态文件写入失败 ({exc})，不影响推送结果", file=sys.stderr)

    for line in form_notes:
        print(f"[zhangyu] {line}")
    _print_card(card, quoteset)
    print(f"✅ 已推送 → 设备 {device_id}")
    print(f"Dot 云返回：{message}")
    return 0


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    now = local_now(cfg["timezone"])
    try:
        quoteset = load_quotes(cfg, args)
    except QuoteError as exc:
        print(f"行情获取失败：{exc}", file=sys.stderr)
        return 1
    title = "" if args.no_title else (args.title or cfg["title"])
    card = build_card(
        quoteset,
        title=title,
        moment=now,
    )
    _print_card(card, quoteset)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


def cmd_indices(_args) -> int:
    print(f"{'键':<8}{'分组':<6}{'显示名':<10}{'东财 secid':<14}新浪代码")
    print("-" * 56)
    for spec in INDEX_SPECS:
        print(f"{spec.key:<8}{spec.group:<6}{spec.label:<10}{spec.em:<14}{spec.sina}")
    print(f"\n共 {len(INDEX_SPECS)} 项批量取数；卡片按顺序只显示其中 {len(CARD_ROWS)} 项：")
    print("  " + " → ".join(label for _, label in CARD_ROWS))
    return 0


def cmd_doctor(args) -> int:
    """自检：配置、凭据、行情源、（可选）固定列表查询。"""
    cfg = load_config(args.config)
    now = local_now(cfg["timezone"])
    print(f"{APP_NAME} v{__version__} 自检")
    print(f"- 今天({cfg['timezone']}): {now:%Y-%m-%d %H:%M} 星期"
          f"{'一二三四五六日'[now.weekday()]}")
    window = in_trading_window(now.astimezone(dt.timezone.utc))
    print(f"- 交易时段: {'是（' + window + '）✅' if window else '否（休市，定时任务会跳过）'}")
    device_id, api_key = read_credentials(cfg)
    problems = missing_credentials(device_id, api_key)
    if "device_id" in problems:
        shown = "未配置 ❌" if not device_id else f"仍是模板占位值 {device_id!r} ❌"
    else:
        shown = f"{device_id} ✅"
    print(f"- device_id: {shown}")
    print(f"- DOT_API_KEY: {'已设置 ✅' if api_key else '未设置 ❌（推送时必需）'}")
    if api_key:
        print(f"    · Key 指纹: {api_key_summary(api_key)}")
        for item in api_key_shape_lines(api_key):
            print(f"    · ⚠️ {item}")
        for item in api_key_shape_hints(api_key):
            print(f"    · 提示（不影响使用，服务端会以实际形态验证）: {item}")
    if swapped_secrets(device_id, api_key):
        print(
            "    · ⚠️ device_id 看起来是 API Key —— 两个 Secret 很可能填反了"
            "（DOT_DEVICE_ID=设备序列号，DOT_API_KEY=dot_app_…）"
        )
    if problems and getattr(args, "require_credentials", False):
        print(f"\n错误：缺少 {', '.join(problems)}。", file=sys.stderr)
        print(CREDENTIAL_HINT, file=sys.stderr)
        return 2
    # 可选：真的拿 Key 去问一次 Dot 云（只读接口，不改设备内容）
    if getattr(args, "check_key", False):
        if problems:
            print("- Key 有效性: 跳过（凭据不全）")
        else:
            try:
                tried = []
                accepted = None
                for token in api_key_candidates(api_key):
                    form = "补前缀" if token == normalize_api_key(api_key) else "原值"
                    tried.append(form)
                    try:
                        accepted = get_fixed_list(
                            device_id=device_id, api_key=token, timeout=10.0
                        )
                    except DotAPIError as exc:
                        if exc.status in (400, 401):
                            continue
                        raise
                if accepted is None and len(tried) > 1:
                    raise DotAPIError(400, "两种 Key 形态都被 Dot 云判为无效")
                if accepted is None:  # 只有一种形态且失败
                    raise DotAPIError(400, "Dot 云判为无效")
                print("- Key 有效性: ✅ Dot 云接受了该 Key 与设备序列号"
                      + (f"（{tried[-1]}形态）" if len(tried) > 1 else ""))
            except DotAPIError as exc:
                if exc.status is None:
                    # 连不上/超时：不能据此断言 Key 无效（CI 里网络抖动很常见）
                    print(f"- Key 有效性: ⚠️ 无法判定（{exc.message}）")
                    print("  → 网络或 Dot 服务临时不可用，换个时间重试即可")
                else:
                    print(f"- Key 有效性: ❌ {exc}")
                if exc.status in (400, 401):
                    print("  → Key 本身无效（生成/复制/失效问题，见下方清单）")
                    api_key_rejected_hint(api_key, device_id)
                elif exc.status == 404:
                    print("  → Key 有效但设备序列号不存在（或未绑定到该账号）")
                elif exc.status == 403:
                    print("  → Key 有效但无权访问该设备")

    # 可选：查这台设备的 loop / fixed 列表里有没有 Text API 内容槽位
    if getattr(args, "check_content", False):
        if problems:
            print("- 内容槽位: 跳过（凭据不全）")
        else:
            print("- 内容槽位（Text API 必须存在于某个列表里，否则推送必然 404）:")
            lists = describe_task_lists(device_id, api_key, 10.0)
            report_task_lists(lists)
            target = pick_text_task(lists["loop"] or []) or pick_text_task(
                lists["fixed"] or []
            )
            if target is None and not lists["errors"]:
                content_missing_hint(str(cfg.get("task_type") or "loop"), "")

    print(f"- source: {cfg['source']}  task_type: {cfg['task_type']}")
    try:
        quoteset = load_quotes(cfg, args)
        card = build_card(quoteset, title=cfg["title"], moment=now)
        lines = card["message"].count("\n") + 1
        print(f"- 行情源: {quoteset.source} ✅（取数 {len(quoteset.quotes)} 项）")
        print(f"- 卡片排版: ✅（6 行 = 标题·日期时间 + {lines} 行指数）")
    except Exception as exc:  # 自检不因异常中断
        print(f"- 行情源/排版: ❌ {exc}")
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #

def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--config", help="配置文件路径（默认 ./config.json，不存在则用内置默认值）")
    sp.add_argument("--fixture", help="使用本地行情 JSON 文件（东财原始响应或归一化格式，离线调试）")
    sp.add_argument("--source", choices=["auto", "em", "sina"],
                    help="行情数据源：auto=东财→新浪→缓存（覆盖配置）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="zhangyu", description=f"{APP_NAME} — 指数行情推送")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} v{__version__}")
    sub = parser.add_subparsers(dest="command")

    p_push = sub.add_parser("push", help="获取最新指数行情并推送到 Dot 设备（默认命令）")
    _add_common(p_push)
    p_push.add_argument("--dry-run", action="store_true", help="只打印卡片，不调用 Dot 云接口")
    p_push.add_argument("--force", action="store_true", help="忽略最近推送状态，强制重推")
    p_push.add_argument("--trading-only", dest="trading_only", action="store_true",
                        help="仅交易时段（A股/港股或美股盘中）才推送，休市跳过（定时任务用）")
    p_push.add_argument("--task-type", dest="task_type", choices=["loop", "fixed"], help="覆盖配置")
    p_push.add_argument("--task-key", dest="task_key", help="fixed 列表项 key（覆盖配置）")
    p_push.add_argument("--timeout", type=float, default=20.0, help="Dot 接口超时秒数")
    p_push.add_argument("--show-payload", action="store_true", help="打印将要发送的请求体")
    p_push.add_argument(
        "--no-title",
        action="store_true",
        help="卡片不带标题（Text API 请求体里不发 title 字段；屏幕/预览也不显示「标题：」）",
    )

    p_preview = sub.add_parser("preview", help="只排版预览卡片（不推送）")
    _add_common(p_preview)
    p_preview.add_argument("--title", help="覆盖卡片标题")
    p_preview.add_argument(
        "--no-title",
        action="store_true",
        help="卡片不带标题（与 --title 互斥，--no-title 优先）",
    )
    p_preview.add_argument("--json", action="store_true", help="额外输出 JSON 形式的卡片")

    p_indices = sub.add_parser("indices", help="查看指数清单（取数 10 项 / 卡片显示 5 项）")
    p_indices.add_argument("--config", help=argparse.SUPPRESS)  # 与其它子命令保持一致

    p_doctor = sub.add_parser("doctor", help="环境自检")
    p_doctor.add_argument("--config", help="配置文件路径")
    p_doctor.add_argument("--fixture", help="离线行情夹具（与 preview 相同）")
    p_doctor.add_argument("--source", choices=["auto", "em", "sina"], help="覆盖配置的数据源")
    p_doctor.add_argument(
        "--require-credentials",
        dest="require_credentials",
        action="store_true",
        help="凭据缺失时以退出码 2 结束（CI 预检用）",
    )
    p_doctor.add_argument(
        "--check-key",
        dest="check_key",
        action="store_true",
        help="调用 Dot 云只读接口验证 API Key 是否有效（不改设备内容）",
    )
    p_doctor.add_argument(
        "--check-content",
        dest="check_content",
        action="store_true",
        help="查询设备的循环/固定列表，确认已添加 Text API 内容（推送 404 的根因）",
    )

    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw)
    if args.command is None:
        # 裸调用等价于 push（顶层没有可解析的业务参数，直接按 push 重解析）
        args = parser.parse_args(["push"])
    command = args.command
    if command == "push":
        return cmd_push(args)
    if command == "preview":
        return cmd_preview(args)
    if command == "indices":
        return cmd_indices(args)
    if command == "doctor":
        return cmd_doctor(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
