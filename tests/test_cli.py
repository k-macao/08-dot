"""CLI 契约测试：行情夹具下的排版/推送、凭据缺失行为、幂等、交易时段门控。

离线保障：配置里永远带 fixture（测试数据），Dot 云的
get_task_list / get_fixed_list 在 setUp 中统一打桩 —— 测试不发真实网络请求。
"""

import datetime as dt
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from zhangyu import cli

FIXTURE = str(Path(__file__).parent / "fixtures" / "quotes_em.json")

CLEAN_ENV = (
    "DOT_DEVICE_ID",
    "DOT_API_KEY",
    "ZHANGYU_TITLE",
    "ZHANGYU_SOURCE",
    "ZHANGYU_KEY_FORM",
)

GOOD_KEY = "dot_app_" + "U" * 64


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k) for k in CLEAN_ENV if k in os.environ}
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config.json"
        # 行情走夹具 + 状态文件进临时目录：不联网、不污染仓库
        self.write_config({})
        # 内容列表查询打桩（push/doctor 解析目标槽位时默认不联网）
        self._p0 = mock.patch.object(cli, "get_task_list", return_value=[])
        self._p1 = mock.patch.object(cli, "get_fixed_list",
                                     return_value={"data": []})
        self._p0.start()
        self._p1.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p0.stop)

    def tearDown(self):
        os.environ.update(self._env)
        self._tmp.cleanup()

    def write_config(self, extra):
        cfg = {
            "fixture": FIXTURE,
            "state_file": str(self.tmp / "state.json"),
        }
        cfg.update(extra)
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--config", str(self.config)])
        return code, out.getvalue(), err.getvalue()

    # 常用小工具 -----------------------------------------------------------
    def write_state(self, **extra):
        path = self.tmp / "state.json"
        path.write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _ago(minutes):
        now = dt.datetime.now(ZoneInfo("Asia/Macau"))
        return (now - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


class DefaultsTest(CLITestCase):
    def test_default_title_and_source(self):
        self.assertEqual(cli.DEFAULTS["title"], "章鱼AI.行情速报")
        self.assertEqual(cli.DEFAULTS["source"], "auto")
        self.assertEqual(cli.DEFAULTS["task_type"], "loop")

    def test_version_and_app_name(self):
        from zhangyu import APP_NAME, __version__
        self.assertEqual(APP_NAME, "章鱼AI.行情速报")
        self.assertEqual(__version__, "2.1.0")

    def test_indices_command_lists_all(self):
        code, out, _ = self.run_cli("indices")
        self.assertEqual(code, 0)
        for key in ("sh", "hstech", "dji", "spx"):
            self.assertIn(key, out)
        self.assertIn("124.HSTECH", out)   # 恒生科技的东财市场号
        self.assertIn("gb_$dji", out)
        self.assertIn("共 10 项", out)


class PreviewTest(CLITestCase):
    def test_preview_shows_six_line_card(self):
        code, out, _ = self.run_cli("preview")
        self.assertEqual(code, 0)
        # 第 1 行：标题 · 日期时间
        self.assertRegex(out, r"标题：章鱼AI\.行情速报 · \d{2}-\d{2} \d{2}:\d{2}")
        # 第 2–6 行：五项指数，指定显示名
        for line in ("上证 3888.12 +0.56%", "恒生指数 24567.89 +0.31%",
                     "恒科 5678.90 -0.66%", "纳斯达克 19876.54 +0.25%",
                     "标普五百 5890.12 +0.18%"):
            self.assertIn(line, out)
        # 只显示这 5 项：分组标题与其余指数都不出现
        for absent in ("A股", "港股", "美股", "深成", "创业板",
                       "沪深300", "恒生国企", "道琼斯"):
            self.assertNotIn(absent, out)
        self.assertNotIn("签名：", out)   # 6 行卡片无独立签名行
        self.assertIn("数据源：fixture(东财)", out)
        self.assertIn("取数 10 项", out)  # 数据层仍批量取 10 项

    def test_preview_title_from_config(self):
        self.write_config({"title": "自定义标题"})
        code, out, _ = self.run_cli("preview")
        self.assertEqual(code, 0)
        self.assertIn("标题：自定义标题", out)

    def test_preview_bad_fixture_fails_cleanly(self):
        self.write_config({"fixture": str(self.tmp / "nope.json")})
        code, _, err = self.run_cli("preview")
        self.assertEqual(code, 1)
        self.assertIn("行情获取失败", err)

    def test_fixture_flag_overrides_config(self):
        # 归一化格式的夹具也能用（load_fixture 双格式）
        norm = self.tmp / "norm.json"
        norm.write_text(json.dumps({
            "source": "fixture(手工)",
            "quotes": [{"key": "sh", "price": 3001.5, "pct": 1.25}],
        }), encoding="utf-8")
        code, out, _ = self.run_cli("preview", "--fixture", str(norm))
        self.assertEqual(code, 0)
        self.assertIn("上证 3001.50 +1.25%", out)
        self.assertIn("数据源：fixture(手工)", out)


class PayloadFlagTest(CLITestCase):
    def test_show_payload_prints_body_without_credentials(self):
        """凭据缺失时 --show-payload 仍应打印请求体，并明确指出缺什么。"""
        code, out, err = self.run_cli("push", "--show-payload")
        self.assertEqual(code, 2)
        self.assertIn('"taskType"', out)
        self.assertIn('"message"', out)
        self.assertIn("deviceId: (未配置)", out)
        self.assertIn("device_id", err)
        self.assertIn("DOT_DEVICE_ID", err)  # 提示里给到 CI 的配置位置

    def test_dry_run_with_payload_needs_no_credentials(self):
        code, out, _ = self.run_cli("push", "--dry-run", "--show-payload")
        self.assertEqual(code, 0)
        self.assertIn('"taskType"', out)
        self.assertIn("[dry-run]", out)
        self.assertIn("上证 3888.12 +0.56%", out)  # 卡片也打印了

    def test_placeholder_device_id_is_treated_as_missing(self):
        self.write_config({"device_id": "YOUR_DEVICE_SERIAL_NUMBER"})
        os.environ["DOT_API_KEY"] = "dot_app_fake"
        code, _, err = self.run_cli("push")
        self.assertEqual(code, 2)
        self.assertIn("device_id", err)

    def test_doctor_flags_placeholder_and_requires_credentials(self):
        self.write_config({"device_id": "YOUR_DEVICE_SERIAL_NUMBER"})
        code, out, err = self.run_cli("doctor", "--require-credentials")
        self.assertEqual(code, 2)
        self.assertIn("占位值", out)
        self.assertIn("DOT_API_KEY", err)

    def test_doctor_passes_when_credentials_present(self):
        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = "dot_app_fake"
        code, out, _ = self.run_cli("doctor", "--require-credentials")
        self.assertEqual(code, 0)
        self.assertIn("ABCD1234 ✅", out)

    def test_doctor_without_flag_still_exits_zero(self):
        """默认 doctor 是"看看就好"，没配密钥也不能让自检失败。"""
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("未配置", out)

    def test_doctor_reports_quotes_and_trading_window(self):
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("交易时段", out)
        self.assertIn("行情源", out)
        self.assertIn("10 项", out)
        self.assertIn("卡片排版", out)


class IdempotencyTest(CLITestCase):
    """每小时一更：50 分钟内的重触发跳过，--force 可穿透。"""

    def test_recent_push_skips(self):
        self.write_state(last_push_at=self._ago(10))
        with mock.patch.object(cli, "push_text", return_value="ok") as push:
            code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        self.assertIn("已推送过", out)
        push.assert_not_called()

    def test_stale_push_goes_through(self):
        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        self.write_state(last_push_at=self._ago(120))
        with mock.patch.object(cli, "push_text", return_value="ok") as push:
            code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        push.assert_called_once()
        self.assertIn("已推送", out)
        saved = json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        # 状态被刷新为本次推送时间（不是旧时间）
        self.assertNotEqual(saved["last_push_at"], self._ago(120))

    def test_recent_but_no_credentials_still_skips_before_error(self):
        self.write_state(last_push_at=self._ago(5))
        code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        self.assertIn("已推送过", out)

    def test_force_bypasses_recent_skip(self):
        self.write_state(last_push_at=self._ago(5))
        code, _, err = self.run_cli("push", "--force")
        self.assertEqual(code, 2)  # 穿透了幂等 → 走到凭据校验
        self.assertNotIn("已推送过", err)
        self.assertIn("device_id", err)

    def test_legacy_last_push_date_does_not_block(self):
        # 旧格式（按日幂等）不拦小时级推送
        today = dt.datetime.now(ZoneInfo("Asia/Macau")).date().isoformat()
        self.write_state(last_push_date=today, sign="leo")
        code, _, err = self.run_cli("push")
        self.assertEqual(code, 2)
        self.assertNotIn("已推送过", err)
        self.assertIn("device_id", err)

    def test_force_with_credentials_pushes(self):
        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        self.write_state(last_push_at=self._ago(5))
        with mock.patch.object(cli, "push_text", return_value="ok") as push:
            code, _, _ = self.run_cli("push", "--force")
        self.assertEqual(code, 0)
        push.assert_called_once()


class TradingOnlyTest(CLITestCase):
    """--trading-only：休市直接跳过（退出码 0），盘中照常推送。"""

    def test_outside_window_skips_without_pushing(self):
        with mock.patch.object(cli, "in_trading_window", return_value=None), \
             mock.patch.object(cli, "push_text", return_value="ok") as push:
            code, out, _ = self.run_cli("push", "--trading-only")
        self.assertEqual(code, 0)
        self.assertIn("非交易时段", out)
        self.assertIn("跳过", out)
        push.assert_not_called()

    def test_inside_window_pushes(self):
        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        with mock.patch.object(cli, "in_trading_window", return_value="A股/港股"), \
             mock.patch.object(cli, "push_text", return_value="ok") as push:
            code, _, _ = self.run_cli("push", "--trading-only")
        self.assertEqual(code, 0)
        push.assert_called_once()

    def test_dry_run_bypasses_gate(self):
        with mock.patch.object(cli, "in_trading_window", return_value=None):
            code, out, _ = self.run_cli("push", "--trading-only", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)

    def test_gate_not_applied_without_flag(self):
        # 手动推送不带 --trading-only → 不查交易时段（走到凭据校验才对）
        with mock.patch.object(cli, "in_trading_window",
                               side_effect=AssertionError("不该被调用")):
            code, out, err = self.run_cli("push")
        self.assertEqual(code, 2)      # 凭据缺失错误 —— 说明没被门控拦下
        self.assertNotIn("非交易时段", out + err)
        self.assertIn("device_id", err)


class NoTitleTest(CLITestCase):
    def test_preview_no_title_omits_title_line(self):
        code, out, _ = self.run_cli("preview", "--no-title", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("标题：", out)
        m = re.search(r'"title"\s*:\s*"([^"]*)"', out)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "")

    def test_push_no_title_body_without_title_field(self):
        code, out, _ = self.run_cli("push", "--dry-run", "--show-payload", "--no-title")
        self.assertEqual(code, 0)
        self.assertIn('"taskType"', out)
        self.assertNotIn('"title"', out)
        self.assertNotIn("标题：", out)


class ApiKeyRejectedTest(CLITestCase):
    """HTTP 400「This API key is invalid」必须有定向排查提示。"""

    def setUp(self):
        super().setUp()
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = '"dot_app_deadbeefdeadbeef"'

    def _patch_push(self, exc):
        return mock.patch.object(cli, "push_text", side_effect=exc)

    def test_invalid_key_prints_targeted_hint(self):
        from zhangyu.dot_client import DotAPIError

        with self._patch_push(DotAPIError(
                400, "This API key is invalid. Check the key and try again.")):
            code, _, err = self.run_cli("push")
        self.assertEqual(code, 1)
        self.assertIn("This API key is invalid", err)
        self.assertIn("实际发送的 Key", err)
        self.assertIn("已清理", err)          # 提示我们清掉了引号
        self.assertIn("重新生成", err)         # 失效 Key 的处理
        self.assertIn("doctor --check-key", err)
        self.assertNotIn("deadbeef", err)     # 绝不回显密钥本体

    def test_swapped_secrets_are_called_out(self):
        from zhangyu.dot_client import DotAPIError

        os.environ["DOT_API_KEY"] = "ABCD1234ABCD"
        self.write_config({"device_id": "dot_app_abcd1234efgh"})
        with self._patch_push(DotAPIError(400, "This API key is invalid.")):
            _, _, err = self.run_cli("push")
        self.assertIn("填反", err)

    def test_403_still_reports_permission(self):
        from zhangyu.dot_client import DotAPIError

        with self._patch_push(DotAPIError(403, "forbidden")):
            code, _, err = self.run_cli("push")
        self.assertEqual(code, 1)
        self.assertIn("403", err)
        self.assertNotIn("填反", err)

    def test_check_key_ok_against_mock_cloud(self):
        with mock.patch.object(cli, "get_fixed_list", return_value={"data": []}):
            code, out, _ = self.run_cli("doctor", "--check-key")
        self.assertEqual(code, 0)
        self.assertIn("Key 有效性: ✅", out)

    def test_check_key_invalid_reports_and_hints(self):
        from zhangyu.dot_client import DotAPIError

        with mock.patch.object(
            cli, "get_fixed_list",
            side_effect=DotAPIError(400, "This API key is invalid."),
        ):
            code, out, err = self.run_cli("doctor", "--check-key")
        self.assertEqual(code, 0)          # 自检不因 Key 无效而崩
        self.assertIn("Key 有效性: ❌", out)
        self.assertIn("Key 本身无效", out)
        self.assertIn("重新生成", err)

    def test_check_key_skipped_without_credentials(self):
        os.environ.pop("DOT_API_KEY")
        self.write_config({})
        code, out, _ = self.run_cli("doctor", "--check-key")
        self.assertEqual(code, 0)
        self.assertIn("跳过", out)


class CheckKeyNetworkTest(CLITestCase):
    def test_network_error_is_inconclusive_not_invalid(self):
        """连不上 Dot 云时不能断言 Key 无效（CI 里网络抖动很常见）。"""
        from zhangyu.dot_client import DotAPIError

        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = "dot_app_abcdefabcdefabcdef"
        with mock.patch.object(
            cli, "get_fixed_list",
            side_effect=DotAPIError(None, "网络错误: timed out"),
        ):
            code, out, err = self.run_cli("doctor", "--check-key")
        self.assertEqual(code, 0)
        self.assertIn("无法判定", out)
        self.assertNotIn("Key 本身无效", out)
        self.assertNotIn("重新生成", err)


class ContentTargetTest(CLITestCase):
    """Text API 内容加在哪个列表，决定推送写哪里（404 的根因）。"""

    LOOP_EMPTY = [{"type": "GENERAL", "key": "gen1"}]
    FIXED_WITH_TEXT = [{"type": "TEXT_API", "key": "txt-fixed-1", "taskAlias": "行情"}]
    LOOP_WITH_TEXT = [{"type": "TEXT_API", "key": "txt-loop-1"}]

    def _lists(self, loop, fixed):
        def fake(task_type, **kwargs):
            return list(loop) if task_type == "loop" else list(fixed)

        return mock.patch.object(cli, "get_task_list", side_effect=fake)

    def test_uses_loop_when_text_api_present(self):
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        with self._lists(self.LOOP_WITH_TEXT, []):
            code, out, _ = self.run_cli("doctor", "--check-content")
        self.assertEqual(code, 0)
        self.assertIn("loop 列表", out)
        self.assertIn("✅ 有 Text API 内容", out)

    def test_reports_missing_text_api_and_app_steps(self):
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        with self._lists(self.LOOP_EMPTY, []):
            code, out, err = self.run_cli("doctor", "--check-content")
        self.assertEqual(code, 0)
        self.assertIn("❌ 没有 Text API 内容", out)
        self.assertIn("内容工坊", err)

    def test_auto_switches_to_fixed_list(self):
        """循环列表没有 Text API、固定列表有 → 自动改用固定列表推送。"""
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        sent = {}

        def fake_push(body, **kwargs):
            sent.update(body)
            return "Device ABCD1234ABCD text API content switched."

        with self._lists(self.LOOP_EMPTY, self.FIXED_WITH_TEXT), \
                mock.patch.object(cli, "push_text", side_effect=fake_push):
            code, _, err = self.run_cli("push")
        self.assertEqual(code, 0)
        self.assertEqual(sent["taskType"], "fixed")
        self.assertEqual(sent["taskKey"], "txt-fixed-1")
        self.assertIn("已自动改用该列表", err)

    def test_explicit_task_type_is_respected(self):
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        sent = {}

        def fake_push(body, **kwargs):
            sent.update(body)
            return "ok"

        with self._lists(self.LOOP_EMPTY, self.FIXED_WITH_TEXT), \
                mock.patch.object(cli, "push_text", side_effect=fake_push):
            code, _, err = self.run_cli("push", "--task-type", "loop")
        self.assertEqual(code, 0)
        self.assertEqual(sent["taskType"], "loop")
        self.assertNotIn("已自动改用该列表", err)


class KeyFormMemoryTest(CLITestCase):
    def test_accepted_raw_form_is_remembered(self):
        """服务端接受原值形态后要记进 state，下次别再先吃一个 400。"""
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = "A1b2C3d4E5f6" * 5 + "G7h8"   # 68 位、无前缀

        def fake_push(body, **kwargs):
            kwargs["info"]["form"] = "raw"
            return "ok"

        with mock.patch.object(cli, "push_text", side_effect=fake_push):
            code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        state = json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["key_form"], "raw")
        self.assertEqual(cli.state_key_form(self.tmp / "state.json"), "raw")

    def test_env_override_wins(self):
        os.environ["ZHANGYU_KEY_FORM"] = "prefixed"
        path = self.tmp / "state.json"
        path.write_text(json.dumps({"key_form": "raw"}), encoding="utf-8")
        self.assertEqual(cli.state_key_form(path), "prefixed")


class PushFlowTest(CLITestCase):
    """成功推送：状态落盘、卡片数据源标注、点位写进 message。"""

    def test_successful_push_records_state(self):
        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        sent = {}

        def fake_push(body, **kwargs):
            sent.update(body)
            return "Device ABCD1234 text API content switched."

        with mock.patch.object(cli, "push_text", side_effect=fake_push):
            code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        self.assertIn("已推送 → 设备 ABCD1234", out)
        self.assertIn("数据源：fixture(东财)", out)
        # 第 1 行在 title 字段：标题 · 日期时间
        self.assertTrue(sent["title"].startswith("章鱼AI.行情速报 · "))
        # 第 2–6 行：五项指数按指定顺序与显示名
        self.assertEqual(
            sent["message"].splitlines(),
            [
                "上证 3888.12 +0.56%",
                "恒生指数 24567.89 +0.31%",
                "恒科 5678.90 -0.66%",
                "纳斯达克 19876.54 +0.25%",
                "标普五百 5890.12 +0.18%",
            ],
        )
        self.assertNotIn("signature", sent)  # 无独立签名行
        saved = json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        self.assertIn("last_push_at", saved)
        self.assertEqual(saved["task_type"], "loop")
        self.assertEqual(saved["source"], "fixture(东财)")

    def test_push_404_reports_content_hint(self):
        from zhangyu.dot_client import DotAPIError

        self.write_config({"device_id": "ABCD1234"})
        os.environ["DOT_API_KEY"] = GOOD_KEY
        with mock.patch.object(cli, "push_text",
                               side_effect=DotAPIError(404, "Not Found")), \
             self._lists_empty():
            code, _, err = self.run_cli("push")
        self.assertEqual(code, 1)
        self.assertIn("404", err)
        self.assertIn("Text API", err)
        self.assertIn("内容工坊", err)

    def _lists_empty(self):
        return mock.patch.object(cli, "get_task_list", return_value=[])


if __name__ == "__main__":
    unittest.main()
