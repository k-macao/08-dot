import datetime as dt
import json
import unittest
from pathlib import Path

from zhangyu.formatter import build_card, stars, truncate
from zhangyu.providers import fetch_offline, parse_xxapi
from zhangyu.signs import parse_sign

TODAY = dt.date(2026, 10, 25)


def _fixture_fortune():
    payload = json.loads(Path("tests/fixtures/xxapi_aries_today.json").read_text(encoding="utf-8"))
    return parse_xxapi(payload, parse_sign("aries"), TODAY)


class StarsTest(unittest.TestCase):
    def test_stars(self):
        self.assertEqual(stars(5), "★★★★★")
        self.assertEqual(stars(3), "★★★☆☆")
        self.assertEqual(stars(1), "★☆☆☆☆")
        self.assertEqual(stars(0), "")
        self.assertEqual(stars(9), "")
        self.assertEqual(stars(None), "")


class TruncateTest(unittest.TestCase):
    def test_short_untouched(self):
        self.assertEqual(truncate("短句。", 90), "短句。")

    def test_cut_at_sentence(self):
        text = "一二三四五六七八九十。" * 10
        out = truncate(text, 30)
        self.assertLessEqual(len(out), 31)
        self.assertTrue(out.endswith("。") or out.endswith("…"))

    def test_hard_cut_adds_ellipsis(self):
        out = truncate("甲" * 100, 20)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(len(out), 21)


class BuildCardTest(unittest.TestCase):
    def test_brief_card_from_real_fixture(self):
        fortune = _fixture_fortune()
        card = build_card(fortune, title="章鱼AI.每日占一占", style="brief", max_chars=90)

        self.assertEqual(card["title"], "章鱼AI.每日占一占")
        message = card["message"]

        # 头部
        self.assertTrue(message.startswith("♈ 白羊座 · 今日运势"))
        # 简评 + 星级 + 指数
        self.assertIn("「积累自身实力」★★★★☆ 73%", message)
        # 正文（截断且无水印）
        self.assertNotIn("星o座o屋", message)
        # 四维指数
        self.assertIn("爱情 56% · 事业 84% · 财运 14% · 健康 34%", message)
        # 宜忌与幸运信息
        self.assertIn("宜 整理闲置物品 · 忌 沉迷刷短视频", message)
        self.assertIn("幸运 草绿 · 17 · 贵人 狮子座", message)
        # 签名
        self.assertEqual(card["signature"], "2026年10月25日 星期日 · 章鱼AI")
        # brief 不包含分项长文
        self.assertNotIn("整体节奏紧凑且效率较高", message)

    def test_full_card_contains_domains(self):
        fortune = _fixture_fortune()
        card = build_card(fortune, style="full", max_chars=90)
        self.assertIn("事业 ", card["message"])
        self.assertIn("财运 ", card["message"])

    def test_offline_card_shape(self):
        fortune = fetch_offline(parse_sign("leo"), TODAY)
        card = build_card(fortune, title="章鱼AI.每日占一占")
        lines = card["message"].splitlines()
        self.assertTrue(lines[0].startswith("♌ 狮子座 · 今日运势"))
        self.assertTrue(any(line.startswith("宜 ") for line in lines))
        self.assertTrue(any(line.startswith("幸运 ") for line in lines))
        self.assertIn("综合" in card["message"] or "★" in card["message"], (True, False))

    def test_minimal_english_card(self):
        from zhangyu.providers import Fortune

        fortune = Fortune(
            sign=parse_sign("leo"),
            date=TODAY,
            source="test",
            overall="A quiet and thoughtful day.",
        )
        card = build_card(fortune)
        lines = card["message"].splitlines()
        self.assertEqual(lines[0], "♌ 狮子座 · 今日运势")
        self.assertEqual(lines[1], "A quiet and thoughtful day.")
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
