"""zhangyu.formatter 卡片排版测试：指数行情 → 三组卡片。"""

import datetime as dt
import json
import unittest
from pathlib import Path

from zhangyu.formatter import build_card, format_pct, format_price, quote_line
from zhangyu.quotes import Quote, QuoteSet, load_fixture

FIXTURES = Path(__file__).parent / "fixtures"
MOMENT = dt.datetime(2026, 11, 10, 16, 5, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def _em_card(**kwargs):
    return build_card(load_fixture(str(FIXTURES / "quotes_em.json")),
                      moment=MOMENT, **kwargs)


class FormatHelpersTest(unittest.TestCase):
    def test_format_price_two_decimals(self):
        self.assertEqual(format_price(3888.12), "3888.12")
        self.assertEqual(format_price(24567.8), "24567.80")

    def test_format_pct_signed(self):
        self.assertEqual(format_pct(0.56), "+0.56%")
        self.assertEqual(format_pct(-0.12), "-0.12%")
        self.assertEqual(format_pct(0.0), "+0.00%")
        self.assertEqual(format_pct(None), "--")

    def test_quote_line_shape(self):
        q = Quote(key="sh", group="A股", label="上证", price=3888.12, pct=0.56)
        self.assertEqual(quote_line(q), "上证 3888.12 +0.56%")
        q2 = Quote(key="dji", group="美股", label="道琼斯", price=47123.45, pct=-0.12)
        self.assertEqual(quote_line(q2), "道琼斯 47123.45 -0.12%")


class BuildCardTest(unittest.TestCase):
    def test_card_contains_three_groups_in_order(self):
        message = _em_card()["message"]
        lines = message.splitlines()
        headers = [ln for ln in lines if ln in ("A股", "港股", "美股")]
        self.assertEqual(headers, ["A股", "港股", "美股"])
        # 组内保持 INDEX_SPECS 顺序
        a_share = lines[lines.index("A股") + 1: lines.index("港股")]
        self.assertTrue(a_share[0].startswith("上证 "))
        self.assertTrue(a_share[1].startswith("深成 "))
        self.assertTrue(a_share[2].startswith("创业板 "))
        self.assertTrue(a_share[3].startswith("沪深300 "))

    def test_card_has_all_10_indices(self):
        lines = _em_card()["message"].splitlines()
        index_lines = [ln for ln in lines if ln not in ("A股", "港股", "美股")]
        self.assertEqual(len(index_lines), 10)
        # 关键数值抽查
        self.assertIn("上证 3888.12 +0.56%", index_lines)
        self.assertIn("恒生科技 5678.90 -0.66%", index_lines)
        self.assertIn("标普500 5890.12 +0.18%", index_lines)

    def test_title_and_signature(self):
        card = _em_card()
        self.assertEqual(card["title"], "章鱼AI.行情速报")
        self.assertEqual(card["signature"],
                         "2026年11月10日 星期二 16:05 · 章鱼AI")

    def test_title_can_be_empty(self):
        card = _em_card(title="")
        self.assertEqual(card["title"], "")

    def test_custom_brand(self):
        card = _em_card(brand="测试品牌")
        self.assertTrue(card["signature"].endswith("· 测试品牌"))

    def test_missing_group_skipped(self):
        quotes = [Quote(key="sh", group="A股", label="上证", price=3000.0, pct=1.0)]
        card = build_card(QuoteSet(quotes=quotes, source="t"), moment=MOMENT)
        self.assertEqual(card["message"], "A股\n上证 3000.00 +1.00%")

    def test_empty_quoteset_gets_placeholder(self):
        card = build_card(QuoteSet(quotes=[], source="t"), moment=MOMENT)
        self.assertEqual(card["message"], "暂无行情数据")

    def test_partial_quotes_only_lists_available(self):
        # 卡片只显示实际取到的行，不虚构缺失项
        qs = load_fixture(str(FIXTURES / "quotes_em.json"))
        qs.quotes = [q for q in qs.quotes if q.group == "美股"]
        card = build_card(qs, moment=MOMENT)
        lines = card["message"].splitlines()
        self.assertEqual(lines[0], "美股")
        self.assertEqual(len(lines), 4)
        self.assertNotIn("A股", lines)


class CardSizeTest(unittest.TestCase):
    """估算屏幕预算：152×296，正文行宽有限，长行依赖设备换行。"""

    def test_line_count_within_budget(self):
        card = _em_card()
        total = 1 + len(card["message"].splitlines()) + 1  # 标题 + 正文 + 签名
        self.assertLessEqual(total, 16)

    def test_signature_includes_build_time_not_data_time(self):
        # 数据时间与排版时间分开：签名取本地当前时间
        earlier = MOMENT - dt.timedelta(hours=3)
        qs = load_fixture(str(FIXTURES / "quotes_em.json"))
        card = build_card(qs, moment=earlier)
        self.assertIn("13:05", card["signature"])


if __name__ == "__main__":
    unittest.main()
