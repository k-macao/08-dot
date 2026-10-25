import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zhangyu.dot_client import DotAPIError, build_body, normalize_api_key, push_text
from zhangyu.formatter import build_card
from zhangyu.providers import fetch_offline
from zhangyu.signs import parse_sign

import datetime as dt


class _Handler(BaseHTTPRequestHandler):
    """测试用假 Dot 云：记录请求并按路径返回。"""

    records = []
    status = 200
    reply = {"message": "Device ABCD1234ABCD text API content switched."}

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


if __name__ == "__main__":
    unittest.main()
