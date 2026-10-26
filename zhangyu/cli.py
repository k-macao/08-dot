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
    api_key_summary,
    build_body,
    get_fixed_list,
    looks_like_api_key,
    push_text,
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


def api_key_rejected_hint(api_key: str, device_id: str) -> None:
    """HTTP 400/401「This API key is invalid」的定向排查清单。"""
    print("提示：Dot 云明确判定 API Key 无效（不是权限问题，也不是设备问题）。", file=sys.stderr)
    print(f"  实际发送的 Key：{api_key_summary(api_key)}", file=sys.stderr)
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

    body = build_body(
        card,
        task_type=args.task_type or cfg["task_type"],
        task_key=args.task_key or cfg.get("task_key"),
        task_alias=cfg.get("task_alias"),
        refresh_now=cfg.get("refresh_now", True),
        icon=cfg.get("icon_url"),
        link=cfg.get("nfc_link"),
    )
    device_id, api_key = read_credentials(cfg)
    problems = missing_credentials(device_id, api_key)

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

    try:
        message = push_text(
            body,
            device_id=device_id,
            api_key=api_key,
            timeout=float(args.timeout),
        )
    except DotAPIError as exc:
        print(f"推送失败：{exc}", file=sys.stderr)
        if exc.status in (400, 401):
            api_key_rejected_hint(api_key, device_id)
        if exc.status == 404:
            print(
                "提示：404 通常是 ① 设备序列号不对 ② 设备的循环/固定列表里还没有添加 Text API 内容"
                "（Dot. App → Content Studio）。",
                file=sys.stderr,
            )
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
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[zhangyu] 警告：状态文件写入失败 ({exc})，不影响推送结果", file=sys.stderr)

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
                get_fixed_list(device_id=device_id, api_key=api_key, timeout=10.0)
                print("- Key 有效性: ✅ Dot 云接受了该 Key 与设备序列号")
            except DotAPIError as exc:
                print(f"- Key 有效性: ❌ {exc}")
                if exc.status in (400, 401):
                    print("  → Key 本身无效（生成/复制/失效问题，见下方清单）")
                    api_key_rejected_hint(api_key, device_id)
                elif exc.status == 404:
                    print("  → Key 有效但设备序列号不存在（或未绑定到该账号）")
                elif exc.status == 403:
                    print("  → Key 有效但无权访问该设备")

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
