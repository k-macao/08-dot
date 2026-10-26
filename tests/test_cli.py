"""CLI 契约测试：凭据缺失时的行为、--show-payload / --dry-run 语义。

复现的现场：GitHub Actions 里没配 DOT_DEVICE_ID，
`python run_daily.py push --show-payload` 直接以退出码 2 结束，
连请求体都没打印出来 —— 这两个开关本来是用来排查这种问题的。
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from zhangyu import cli

CLEAN_ENV = (
    "DOT_DEVICE_ID",
    "DOT_API_KEY",
    "ZHANGYU_SIGN",
    "ZHANGYU_STYLE",
    "ZHANGYU_TITLE",
)


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k) for k in CLEAN_ENV if k in os.environ}
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = self.tmp / "config.json"
        # 只走离线引擎 + 状态文件放进临时目录：不联网、不污染仓库
        self.write_config({})

    def tearDown(self):
        os.environ.update(self._env)
        self._tmp.cleanup()

    def write_config(self, extra):
        cfg = {
            "sign_mode": "fixed",
            "sign": "leo",
            "providers": ["offline"],
            "state_file": str(self.tmp / "state.json"),
        }
        cfg.update(extra)
        self.config.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([*argv, "--config", str(self.config)])
        return code, out.getvalue(), err.getvalue()


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
        """--dry-run 是纯本地预览，没有凭据也必须成功退出。"""
        code, out, _ = self.run_cli("push", "--dry-run", "--show-payload")
        self.assertEqual(code, 0)
        self.assertIn('"taskType"', out)
        self.assertIn("[dry-run]", out)

    def test_placeholder_device_id_is_treated_as_missing(self):
        """照抄 config.example.json 的占位值不应打到 Dot 云换一个 404。"""
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
        """默认 doctor 是"看看就好"，不能因为没配密钥就让本地自检失败。"""
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("未配置", out)


class IdempotencyTest(CLITestCase):
    def test_same_day_skip(self):
        state = self.tmp / "state.json"
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Macau")).date().isoformat()
        state.write_text(json.dumps({"last_push_date": today}), encoding="utf-8")
        code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        self.assertIn("已推送过", out)

    def test_force_bypasses_skip(self):
        """--force 时不能因为状态文件而提前返回（这里会因缺凭据而退出 2）。"""
        state = self.tmp / "state.json"
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Macau")).date().isoformat()
        state.write_text(json.dumps({"last_push_date": today}), encoding="utf-8")
        code, _, err = self.run_cli("push", "--force")
        self.assertEqual(code, 2)
        self.assertIn("device_id", err)


if __name__ == "__main__":
    unittest.main()


class ApiKeyRejectedTest(CLITestCase):
    """HTTP 400「This API key is invalid」必须有定向排查提示。"""

    def setUp(self):
        super().setUp()
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = '"dot_app_deadbeefdeadbeef"'

    def _patch_push(self, exc):
        from unittest import mock

        return mock.patch.object(cli, "push_text", side_effect=exc)

    def test_invalid_key_prints_targeted_hint(self):
        from zhangyu.dot_client import DotAPIError

        with self._patch_push(DotAPIError(400, "This API key is invalid. Check the key and try again.")):
            code, _, err = self.run_cli("push")
        self.assertEqual(code, 1)
        self.assertIn("This API key is invalid", err)
        self.assertIn("实际发送的 Key", err)
        self.assertIn("已清理", err)          # 提示我们清掉了引号
        self.assertIn("重新生成", err)         # 失效 Key 的处理
        self.assertIn("doctor --check-key", err)
        # 绝不回显密钥本体
        self.assertNotIn("deadbeef", err)

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
        from unittest import mock

        with mock.patch.object(cli, "get_fixed_list", return_value={"data": []}):
            code, out, _ = self.run_cli("doctor", "--check-key")
        self.assertEqual(code, 0)
        self.assertIn("Key 有效性: ✅", out)

    def test_check_key_invalid_reports_and_hints(self):
        from unittest import mock
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
        from unittest import mock
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
    FIXED_WITH_TEXT = [{"type": "TEXT_API", "key": "txt-fixed-1", "taskAlias": "运势"}]
    LOOP_WITH_TEXT = [{"type": "TEXT_API", "key": "txt-loop-1"}]

    def _lists(self, loop, fixed):
        from unittest import mock

        def fake(task_type, **kwargs):
            return list(loop) if task_type == "loop" else list(fixed)

        return mock.patch.object(cli, "get_task_list", side_effect=fake)

    def test_uses_loop_when_text_api_present(self):
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = "dot_app_" + "U" * 64
        with self._lists(self.LOOP_WITH_TEXT, []):
            code, out, _ = self.run_cli("doctor", "--check-content")
        self.assertEqual(code, 0)
        self.assertIn("loop 列表", out)
        self.assertIn("✅ 有 Text API 内容", out)

    def test_reports_missing_text_api_and_app_steps(self):
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = "dot_app_" + "U" * 64
        with self._lists(self.LOOP_EMPTY, []):
            code, out, err = self.run_cli("doctor", "--check-content")
        self.assertEqual(code, 0)
        self.assertIn("❌ 没有 Text API 内容", out)
        self.assertIn("内容工坊", err)

    def test_auto_switches_to_fixed_list(self):
        """循环列表没有 Text API、固定列表有 → 自动改用固定列表推送。"""
        self.write_config({"device_id": "ABCD1234ABCD"})
        os.environ["DOT_API_KEY"] = "dot_app_" + "U" * 64
        from unittest import mock

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
        os.environ["DOT_API_KEY"] = "dot_app_" + "U" * 64
        from unittest import mock

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

        from unittest import mock

        def fake_push(body, **kwargs):
            kwargs["info"]["form"] = "raw"
            return "ok"

        with mock.patch.object(cli, "push_text", side_effect=fake_push), \
                mock.patch.object(cli, "resolve_content_target",
                                 return_value=("loop", None)):
            code, out, _ = self.run_cli("push")
        self.assertEqual(code, 0)
        state = json.loads((self.tmp / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["key_form"], "raw")
        self.assertEqual(cli.state_key_form(self.tmp / "state.json"), "raw")

    def test_env_override_wins(self):
        self.write_config({})
        os.environ["ZHANGYU_KEY_FORM"] = "prefixed"
        path = self.tmp / "state.json"
        path.write_text(json.dumps({"key_form": "raw"}), encoding="utf-8")
        self.assertEqual(cli.state_key_form(path), "prefixed")
