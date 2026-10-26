import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zhangyu.dot_client import (
    DotAPIError,
    api_key_candidates,
    api_key_health,
    api_key_summary,
    build_body,
    clean_api_key,
    looks_like_api_key,
    normalize_api_key,
    push_text,
)
from zhangyu.formatter import build_card
from zhangyu.providers import fetch_offline
from zhangyu.signs import parse_sign

import datetime as dt


class _Handler(BaseHTTPRequestHandler):
    """测试用假 Dot 云：记录请求并按路径返回。"""

    records = []
    status = 200
    reply = {"message": "Device ABCD1234ABCD text API content switched."}
    # 模拟"服务端只认原值形态（不带 dot_app_ 前缀）"，用于验证自动换形重试
    reject_prefixed = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _Handler.records.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type", ""),
                "body": json.loads(body.decode("utf-8")) if body else None,
            }
        )
        auth = self.headers.get("Authorization") or ""
        if _Handler.reject_prefixed and auth.startswith("Bearer dot_app_"):
            payload = json.dumps(
                {"message": "This API key is invalid. Check the key and try again."}
            ).encode("utf-8")
            self.send_response(400)
        else:
            payload = json.dumps(_Handler.reply).encode("utf-8")
            self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音日志
        pass


class DotClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.records = []
        _Handler.status = 200
        _Handler.reject_prefixed = False
        _Handler.reply = {"message": "Device ABCD1234ABCD text API content switched."}

    def _card(self):
        fortune = fetch_offline(parse_sign("leo"), dt.date(2026, 10, 25))
        return build_card(fortune)

    def test_normalize_api_key(self):
        self.assertEqual(normalize_api_key("abc123"), "dot_app_abc123")
        self.assertEqual(normalize_api_key("dot_app_abc123"), "dot_app_abc123")
        with self.assertRaises(DotAPIError):
            normalize_api_key("  ")

    def test_build_body_defaults(self):
        body = build_body({"title": "T", "message": "M", "signature": "S"})
        self.assertEqual(body["taskType"], "loop")
        self.assertTrue(body["refreshNow"])
        self.assertEqual(body["title"], "T")
        self.assertEqual(body["message"], "M")
        self.assertEqual(body["signature"], "S")
        self.assertNotIn("taskKey", body)

    def test_build_body_fixed(self):
        body = build_body({"message": "M"}, task_type="fixed", task_key="KEY1", refresh_now=False)
        self.assertEqual(body["taskType"], "fixed")
        self.assertEqual(body["taskKey"], "KEY1")
        self.assertFalse(body["refreshNow"])
        with self.assertRaises(DotAPIError):
            build_body({"message": "M"}, task_type="everything")

    def test_push_success_request_shape(self):
        body = build_body(self._card())
        message = push_text(
            body,
            device_id="ABCD1234ABCD",
            api_key="secret-key",
            base_url=self.base_url,
            retries=1,
        )
        self.assertIn("switched", message)

        self.assertEqual(len(_Handler.records), 1)
        record = _Handler.records[0]
        self.assertEqual(record["path"], "/api/authV2/open/device/ABCD1234ABCD/text")
        self.assertEqual(record["authorization"], "Bearer dot_app_secret-key")
        self.assertIn("application/json", record["content_type"])
        sent = record["body"]
        self.assertEqual(sent["taskType"], "loop")
        self.assertIn("章鱼AI.每日占一占", sent["title"])
        self.assertIn("\n", sent["message"])  # 换行保留

    def test_push_404_raises(self):
        _Handler.status = 404
        _Handler.reply = {"message": "API content missing in list"}
        with self.assertRaises(DotAPIError) as ctx:
            push_text(
                build_body({"message": "M"}),
                device_id="BAD",
                api_key="k",
                base_url=self.base_url,
                retries=1,
            )
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("API content missing", str(ctx.exception))

    def test_push_missing_device(self):
        with self.assertRaises(DotAPIError):
            push_text(build_body({"message": "M"}), device_id="", api_key="k")


class ApiKeyCleaningTest(unittest.TestCase):
    """粘贴污染（引号/空白/Bearer/尖括号）必须被清掉。

    现场依据：Dot 云对 key 逐字符比对，多一个引号就返回
    `HTTP 400 This API key is invalid. Check the key and try again.`
    """

    def test_plain_key_untouched(self):
        self.assertEqual(normalize_api_key("dot_app_abc123"), "dot_app_abc123")

    def test_prefix_added_when_missing(self):
        self.assertEqual(normalize_api_key("abc123"), "dot_app_abc123")

    def test_surrounding_quotes_and_spaces(self):
        for raw in ('"dot_app_abc123"', "' dot_app_abc123 '", "\u201cdot_app_abc123\u201d"):
            self.assertEqual(normalize_api_key(raw), "dot_app_abc123", raw)

    def test_bearer_prefix(self):
        self.assertEqual(normalize_api_key("Bearer dot_app_abc123"), "dot_app_abc123")
        self.assertEqual(normalize_api_key("bearer: dot_app_abc123"), "dot_app_abc123")

    def test_inner_whitespace(self):
        self.assertEqual(normalize_api_key("dot_app_abc 123"), "dot_app_abc123")
        self.assertEqual(normalize_api_key("dot_app_abc\n123"), "dot_app_abc123")

    def test_angle_brackets(self):
        self.assertEqual(normalize_api_key("<dot_app_abc123>"), "dot_app_abc123")

    def test_empty_still_raises(self):
        with self.assertRaises(DotAPIError):
            normalize_api_key("   ")

    def test_clean_reports_what_it_fixed(self):
        key, notes = clean_api_key('"Bearer dot_app_abc123"')
        self.assertEqual(key, "dot_app_abc123")
        self.assertIn("引号", notes)
        self.assertIn("Bearer 前缀", notes)

    def test_summary_never_leaks_secret(self):
        secret = "dot_app_supersecretvalue123456"
        summary = api_key_summary(secret)
        self.assertNotIn("supersecret", summary)
        self.assertIn("长度", summary)
        self.assertIn("dot_app_ 前缀", summary)

    def test_summary_flags_quotes_as_cleaned(self):
        summary = api_key_summary('"dot_app_supersecretvalue123456"')
        self.assertIn("引号", summary)
        self.assertNotIn("supersecret", summary)

    def test_looks_like_api_key(self):
        self.assertTrue(looks_like_api_key("dot_app_abc123"))
        self.assertTrue(looks_like_api_key("dot_dev_abc123"))
        self.assertFalse(looks_like_api_key("ABCD1234ABCD"))
        self.assertFalse(looks_like_api_key(""))


class ApiKeyShapeTest(unittest.TestCase):
    """按官方文档的 token 形状做软校验：dot_app_ + 64 位 = 72 字符。"""

    REAL = "dot_app_" + "U" * 64

    def test_real_format_is_clean(self):
        health = api_key_health(self.REAL)
        self.assertTrue(health["looks_valid"])
        self.assertTrue(health["has_prefix"])
        self.assertEqual(health["body_len"], 64)
        self.assertEqual(health["expected_total_len"], 72)

    def test_missing_prefix_is_reported_with_numbers(self):
        health = api_key_health("x" * 68)
        self.assertFalse(health["looks_valid"])
        joined = " ".join(health["problems"])
        self.assertIn("没有 dot_app_ 前缀", joined)
        self.assertIn("68", joined)
        self.assertIn("76", joined)   # 补前缀后的长度
        self.assertIn("64", joined)   # 官方 payload 位数

    def test_bare_payload_is_flagged_but_fixable(self):
        health = api_key_health("U" * 64)
        self.assertEqual(health["body_len"], 64)
        self.assertFalse(health["has_prefix"])
        self.assertEqual(api_key_candidates("U" * 64)[0], self.REAL)

    def test_foreign_token_detected_by_charset(self):
        health = api_key_health("ghp_" + "a1b2c3d4e5f6" * 4)
        self.assertIn("其它服务的 token", " ".join(health["problems"]))

    def test_candidates_dedupe_when_already_prefixed(self):
        self.assertEqual(api_key_candidates(self.REAL), [self.REAL])

    def test_candidates_offer_both_forms(self):
        forms = api_key_candidates("U" * 64)
        self.assertEqual(len(forms), 2)
        self.assertEqual(forms[0], self.REAL)
        self.assertEqual(forms[1], "U" * 64)


class ApiKeyFormFallbackTest(DotClientTest):
    """补前缀被拒（400）时自动改用原值重试。"""

    def test_falls_back_to_raw_form(self):
        _Handler.reject_prefixed = True
        note = []
        message = push_text(
            build_body({"title": "T", "message": "M"}),
            device_id="ABCD1234ABCD",
            api_key="U" * 64,
            base_url=self.base_url,
            retries=1,
            note=note,
        )
        self.assertIn("switched", message)
        self.assertEqual(len(_Handler.records), 2)
        self.assertTrue(_Handler.records[0]["authorization"].startswith("Bearer dot_app_"))
        self.assertEqual(_Handler.records[1]["authorization"], "Bearer " + "U" * 64)
        self.assertTrue(any("原值" in line for line in note))

    def test_both_forms_rejected_raises_400(self):
        _Handler.reject_prefixed = True
        with self.assertRaises(DotAPIError):
            push_text(
                build_body({"title": "T", "message": "M"}),
                device_id="ABCD1234ABCD",
                api_key="dot_app_" + "U" * 64,
                base_url=self.base_url,
                retries=1,
            )
        # 已经是规范形态，无需换形
        self.assertEqual(len(_Handler.records), 1)


if __name__ == "__main__":
    unittest.main()
