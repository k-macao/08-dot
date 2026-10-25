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
