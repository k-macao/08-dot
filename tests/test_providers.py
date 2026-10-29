import datetime as dt
import unittest
from unittest import mock

from zhangyu import providers
from zhangyu.providers import ProviderError, fetch_offline, get_fortune, parse_xxapi, strip_watermark
from zhangyu.signs import parse_sign


FIXTURE = "tests/fixtures/xxapi_aries_today.json"
TODAY = dt.date(2026, 10, 25)


class WatermarkTest(unittest.TestCase):
    def test_strip_variants(self):
        self.assertEqual(strip_watermark("运势不错。星o座o屋"), "运势不错。")
        self.assertEqual(strip_watermark("运势不错。星#座#屋"), "运势不错。")
        self.assertEqual(strip_watermark("运势不错。星8座8屋"), "运势不错。")
        self.assertEqual(strip_watermark("运势不错。星座屋"), "运势不错。")
        self.assertEqual(strip_watermark("正文  中间\n换行"), "正文 中间 换行")


class XxapiParseTest(unittest.TestCase):
    def _load(self):
        import json
        from pathlib import Path

        return json.loads(Path(FIXTURE).read_text(encoding="utf-8"))

    def test_parse_fixture(self):
        fortune = parse_xxapi(self._load(), parse_sign("aries"), TODAY)
        self.assertEqual(fortune.sign.key, "aries")
        self.assertEqual(fortune.sign.cn, "白羊座")
        self.assertEqual(fortune.period, "今日运势")
        self.assertEqual(fortune.scores["all"], 4)
        self.assertEqual(fortune.indices["work"], "84%")
        self.assertEqual(fortune.lucky_color, "草绿")
        self.assertEqual(fortune.lucky_number, "17")
        self.assertEqual(fortune.lucky_buddy, "狮子座")
        self.assertEqual(fortune.yi, "宜整理闲置物品")
        self.assertEqual(fortune.ji, "忌沉迷刷短视频")
        self.assertIn("love", fortune.domains)
        # 水印被清除
        self.assertNotIn("星", fortune.overall[-4:])
        self.assertTrue(fortune.overall.endswith("积极可控。"))

    def test_missing_name_keeps_requested_leo(self):
        payload = self._load()
        payload["data"].pop("name")
        fortune = parse_xxapi(payload, parse_sign("leo"), TODAY)
        self.assertEqual(fortune.sign.key, "leo")

    def test_wrong_sign_response_is_rejected_not_relabeled(self):
        with self.assertRaisesRegex(ProviderError, "与请求的 狮子座 不符"):
            parse_xxapi(self._load(), parse_sign("leo"), TODAY)

    def test_wrong_sign_response_falls_back_to_leo_offline(self):
        with mock.patch.object(providers, "_http_get_json", return_value=self._load()), \
             mock.patch("sys.stderr"):
            fortune = get_fortune(parse_sign("leo"), TODAY, providers=["xxapi", "offline"])
        self.assertEqual(fortune.sign.key, "leo")
        self.assertTrue(fortune.offline)
        self.assertIn("经降级", fortune.source)

    def test_parse_error_payload(self):
        from zhangyu.providers import ProviderError

        with self.assertRaises(ProviderError):
            parse_xxapi({"code": 500, "msg": "限流"}, parse_sign("aries"), TODAY)


class OfflineEngineTest(unittest.TestCase):
    def test_deterministic_per_day_sign(self):
        sign = parse_sign("leo")
        a = fetch_offline(sign, TODAY)
        b = fetch_offline(sign, TODAY)
        self.assertEqual(a.overall, b.overall)
        self.assertEqual(a.scores, b.scores)
        self.assertEqual(a.lucky_color, b.lucky_color)
        self.assertTrue(a.offline)
        c = fetch_offline(parse_sign("virgo"), TODAY)
        self.assertNotEqual(a.scores, c.scores)

    def test_fields_present(self):
        fortune = fetch_offline(parse_sign("aries"), TODAY)
        self.assertIn(fortune.scores["all"], (3, 4, 5))
        self.assertTrue(fortune.overall)
        self.assertTrue(fortune.short_comment)
        self.assertTrue(fortune.yi)  # 离线引擎存原始建议文案，「宜/忌」前缀由排版层补
        self.assertTrue(fortune.ji)
        self.assertTrue(fortune.lucky_number.isdigit())


class FreeHoroscopeParseTest(unittest.TestCase):
    def test_parse_sample(self):
        from zhangyu.providers import parse_freehoroscope

        payload = {
            "data": {
                "date": "2026-10-25",
                "period": "daily",
                "sign": "Leo",
                "horoscope": "Leos may find themselves taking a step back today.",
            }
        }
        fortune = parse_freehoroscope(payload, parse_sign("leo"), TODAY)
        self.assertEqual(fortune.source, "Free Horoscope API（en）")
        self.assertIn("step back", fortune.overall)
        self.assertEqual(fortune.sign.key, "leo")


if __name__ == "__main__":
    unittest.main()
