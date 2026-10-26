"""章鱼AI.每日占一占 — 命令行入口。

用法：
    python run_daily.py push      [--sign leo|狮子座|random] [--style brief|full]
                                  [--dry-run] [--show-payload] [--force]
    python run_daily.py preview   [--sign ...] [--fixture path.json] [--provider offline]
    python run_daily.py signs
    python run_daily.py doctor    [--require-credentials]   # CI 预检用

    没有凭据也能自检：push --dry-run --show-payload 打印排版与请求体后正常退出。
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
from .formatter import build_card
from .providers import Fortune, get_fortune, parse_xxapi
from .signs import SIGNS, parse_sign, random_sign

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

DEFAULTS: Dict[str, Any] = {
    "device_id": "",
    "title": APP_NAME,
    "brand": "章鱼AI",
    "sign_mode": "random",  # random=每日抽签 | fixed=固定星座
    "sign": "aries",  # sign_mode=fixed 时生效
    "style": "brief",  # brief | full
    "max_chars": 90,
    "task_type": "loop",  # loop | fixed
    "task_key": None,  # fixed 列表项的 key（可用 doctor 查询）
    "task_alias": APP_NAME,
    "refresh_now": True,
    "providers": ["xxapi", "freehoroscope", "offline"],
    "icon_url": None,
    "nfc_link": None,
    "timezone": "Asia/Macau",
    "state_file": "data/state.json",
}


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
    elif config_path.exists():
        pass  # 自动读取 ./config.json
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"配置文件不是合法 JSON: {config_path} ({exc})")
        cfg = _deep_merge(cfg, file_cfg)

    # 环境变量覆盖（密钥类只走环境变量）
    if os.environ.get("DOT_DEVICE_ID"):
        cfg["device_id"] = os.environ["DOT_DEVICE_ID"].strip()
    if os.environ.get("ZHANGYU_SIGN"):
        cfg["sign"] = os.environ["ZHANGYU_SIGN"].strip()
        cfg["sign_mode"] = "fixed"
    if os.environ.get("ZHANGYU_STYLE"):
        cfg["style"] = os.environ["ZHANGYU_STYLE"].strip()
    if os.environ.get("ZHANGYU_TITLE"):
        cfg["title"] = os.environ["ZHANGYU_TITLE"].strip()
    return cfg


def local_today(tz_name: str) -> dt.date:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(minutes=480))  # 兜底 UTC+8
        print(f"[zhangyu] 未知时区 {tz_name!r}，使用 UTC+8", file=sys.stderr)
    return dt.datetime.now(tz).date()


def resolve_sign(cfg: Dict[str, Any], arg: Optional[str], today: dt.date):
    """决定本次要“占”的星座：命令行 > 环境/配置 > random 抽签。"""
    if arg and arg.lower() != "random":
        return parse_sign(arg)
    if arg and arg.lower() == "random":
        return random_sign(seed=f"{today.isoformat()}|zhangyu")
    if str(cfg.get("sign_mode", "random")).lower() == "fixed":
        return parse_sign(str(cfg.get("sign") or "aries"))
    # 每日抽签：同一天多次运行结果一致（幂等）
    return random_sign(seed=f"{today.isoformat()}|zhangyu")


def load_fortune(sign, today: dt.date, cfg: Dict[str, Any], args) -> Fortune:
    if getattr(args, "fixture", None):
        payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        fortune = parse_xxapi(payload, sign, today)
        fortune.source += "（fixture）"
        return fortune
    providers = None
    if getattr(args, "provider", None):
        providers = [args.provider]
    return get_fortune(sign, today, providers=providers, timeout=15.0)


def _print_card(card: Dict[str, Any], fortune: Fortune) -> None:
    print("=" * 46)
    print(f"标题：{card['title']}")
    print("-" * 46)
    print(card["message"])
    print("-" * 46)
    print(f"签名：{card['signature']}")
    print(f"数据源：{fortune.source}")
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
    改完后重跑 workflow（Actions → 每日占星推送 → Run workflow）。
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
            f"但『{'固定' if other_type == 'fixed' else '循环'}列表』里有 —— 已自动改用该列表推送。",
            file=sys.stderr,
        )
        print(
            f"[zhangyu] 想固定下来：python run_daily.py push --task-type {other_type}"
            + (f" --task-key {other.get('key')}" if other.get('key') else ""),
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
    today = local_today(cfg["timezone"])
    sign = resolve_sign(cfg, args.sign, today)

    fortune = load_fortune(sign, today, cfg, args)
    card = build_card(
        fortune,
        title=cfg["title"],
        style=args.style or cfg["style"],
        max_chars=int(args.max_chars or cfg["max_chars"]),
        brand=cfg.get("brand") or "章鱼AI",
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
        _print_card(card, fortune)
        if problems:
            print(f"[dry-run] 缺 {', '.join(problems)}；仅本地排版/请求体预览，未推送。")
        else:
            print("[dry-run] 未调用 Dot 云接口。")
        return 0

    # 幂等：当天已推送过则跳过
    state_path = Path(cfg["state_file"])
    if state_path.exists() and not args.force:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
        if state.get("last_push_date") == today.isoformat():
            print(f"[zhangyu] 今日 ({today.isoformat()}) 已推送过，跳过；如需重推加 --force")
            return 0

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
                    "last_push_date": today.isoformat(),
                    "sign": sign.key,
                    "source": fortune.source,
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
    _print_card(card, fortune)
    print(f"✅ 已推送 → 设备 {device_id}")
    print(f"Dot 云返回：{message}")
    return 0


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    today = local_today(cfg["timezone"])
    sign = resolve_sign(cfg, args.sign, today)
    fortune = load_fortune(sign, today, cfg, args)
    card = build_card(
        fortune,
        title=args.title or cfg["title"],
        style=args.style or cfg["style"],
        max_chars=int(args.max_chars or cfg["max_chars"]),
        brand=cfg.get("brand") or "章鱼AI",
    )
    _print_card(card, fortune)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


def cmd_signs(_args) -> int:
    print(f"{'key':<12}{'中文名':<8}{'符号':<4}日期范围")
    print("-" * 40)
    for s in SIGNS:
        print(f"{s.key:<12}{s.cn:<8}{s.symbol:<4}{s.date_range}")
    print("\nsign_mode=random 时每天自动抽签；fixed 时取配置中的 sign。")
    return 0


def cmd_doctor(args) -> int:
    """自检：配置、凭据、数据源、（可选）固定列表查询。"""
    cfg = load_config(args.config)
    today = local_today(cfg["timezone"])
    print(f"{APP_NAME} v{__version__} 自检")
    print(f"- 今天({cfg['timezone']}): {today.isoformat()}")
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
            "    · ⚠️ device_id 看起来是 API Key —— 两个 Secret 可能填反了"
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

    print(f"- sign_mode: {cfg['sign_mode']}  style: {cfg['style']}  task_type: {cfg['task_type']}")

    sign = resolve_sign(cfg, None, today)
    print(f"- 今日抽签星座: {sign.cn} ({sign.key})")
    try:
        fortune = get_fortune(sign, today, providers=cfg.get("providers"), timeout=10.0)
        print(f"- 数据源: {fortune.source} ✅")
        card = build_card(fortune, title=cfg["title"], style=cfg["style"], max_chars=int(cfg["max_chars"]))
        print(f"- 卡片排版: ✅（message {len(card['message'])} 字）")
    except Exception as exc:  # 自检不因异常中断
        print(f"- 数据源/排版: ❌ {exc}")
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #

def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--config", help="配置文件路径（默认 ./config.json，不存在则用内置默认值）")
    sp.add_argument("--sign", help="星座：英文/中文/符号，或 random（覆盖配置）")
    sp.add_argument("--style", choices=["brief", "full"], help="卡片样式（覆盖配置）")
    sp.add_argument("--max-chars", dest="max_chars", type=int, help="综合运势最大字数（覆盖配置）")
    sp.add_argument("--fixture", help="使用本地 xxapi 响应 JSON 文件（离线调试）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="zhangyu", description=f"{APP_NAME} — 每日占星运程推送")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} v{__version__}")
    sub = parser.add_subparsers(dest="command")

    p_push = sub.add_parser("push", help="获取今日运势并推送到 Dot 设备（默认命令）")
    _add_common(p_push)
    p_push.add_argument("--dry-run", action="store_true", help="只打印卡片，不调用 Dot 云接口")
    p_push.add_argument("--force", action="store_true", help="忽略当天已推送状态，强制重推")
    p_push.add_argument("--provider", help="指定数据源：xxapi | freehoroscope | offline")
    p_push.add_argument("--task-type", dest="task_type", choices=["loop", "fixed"], help="覆盖配置")
    p_push.add_argument("--task-key", dest="task_key", help="fixed 列表项 key（覆盖配置）")
    p_push.add_argument("--timeout", type=float, default=20.0, help="Dot 接口超时秒数")
    p_push.add_argument("--show-payload", action="store_true", help="打印将要发送的请求体")

    p_preview = sub.add_parser("preview", help="只排版预览卡片（不推送）")
    _add_common(p_preview)
    p_preview.add_argument("--provider", help="指定数据源：xxapi | freehoroscope | offline")
    p_preview.add_argument("--title", help="覆盖卡片标题")
    p_preview.add_argument("--json", action="store_true", help="额外输出 JSON 形式的卡片")

    sub.add_parser("signs", help="列出全部星座及识别写法")

    p_doctor = sub.add_parser("doctor", help="环境自检")
    p_doctor.add_argument("--config", help="配置文件路径")
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

    args = parser.parse_args(argv)
    command = args.command or "push"
    if command == "push":
        return cmd_push(args)
    if command == "preview":
        return cmd_preview(args)
    if command == "signs":
        return cmd_signs(args)
    if command == "doctor":
        return cmd_doctor(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
