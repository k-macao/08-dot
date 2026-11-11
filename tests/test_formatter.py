"""zhangyu.formatter 卡片排版测试：6 行卡片（标题·日期时间 + 5 项指数）。"""

import datetime as dt
import unittest
from pathlib import Path

from zhangyu.formatter import (
    CARD_ROWS,
    build_card,
    format_pct,
    format_price,
    quote_line,
)
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
        self.assertEqual(quote_line("上证", q), "上证 3888.12 +0.56%")


class BuildCardTest(unittest.TestCase):
    def test_card_rows_are_the_five_requested(self):
        self.assertEqual(
            CARD_ROWS,
            (("sh", "上证"), ("hsi", "恒生指数"), ("hstech", "恒科"),
             ("ixic", "纳斯达克"), ("spx", "标普五百")),
        )

    def test_six_lines_with_title_and_datetime_first(self):
        card = _em_card()
        lines = card["message"].splitlines()
        self.assertEqual(len(lines), 5)  # 第 2–6 行；第 1 行是 title 字段
        self.assertEqual(lines[0], "上证 3888.12 +0.56%")
        self.assertEqual(lines[1], "恒生指数 24567.89 +0.31%")
        self.assertEqual(lines[2], "恒科 5678.90 -0.66%")
        self.assertEqual(lines[3], "纳斯达克 19876.54 +0.25%")
        self.assertEqual(lines[4], "标普五百 5890.12 +0.18%")
        # 第 1 行 = 标题 · 日期时间
        self.assertEqual(card["title"], "章鱼AI.行情速报 · 11-10 16:05")
        # 6 行卡片不再有独立签名行
        self.assertEqual(card["signature"], "")

    def test_untouched_indices_absent_from_card(self):
        """深成/创业板/沪深300/恒生国企/道琼斯 只取数不显示。"""
        message = _em_card()["message"]
        for absent in ("深成", "创业板", "沪深300", "恒生国企", "道琼斯",
                       "A股", "港股", "美股"):
            self.assertNotIn(absent, message)

    def test_title_can_be_empty_moves_stamp_to_message(self):
        """--no-title：title 字段省略，日期时间挪到 message 首行，总行数仍为 6。"""
        card = _em_card(title="")
        self.assertEqual(card["title"], "")
        lines = card["message"].splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0], "11-10 16:05")
        self.assertEqual(lines[1], "上证 3888.12 +0.56%")
        self.assertEqual(card["signature"], "")

    def test_custom_title_prefixes_stamp(self):
        card = _em_card(title="自定义标题")
        self.assertEqual(card["title"], "自定义标题 · 11-10 16:05")

    def test_missing_index_skips_its_line(self):
        qs = load_fixture(str(FIXTURES / "quotes_em.json"))
        qs.quotes = [q for q in qs.quotes if q.key in ("sh", "spx")]
        card = build_card(qs, moment=MOMENT)
        self.assertEqual(card["message"].splitlines(),
                         ["上证 3888.12 +0.56%", "标普五百 5890.12 +0.18%"])

    def test_empty_quoteset_gets_placeholder(self):
        card = build_card(QuoteSet(quotes=[], source="t"), moment=MOMENT)
        self.assertEqual(card["message"], "暂无行情数据")
        self.assertTrue(card["title"].startswith("章鱼AI.行情速报 ·"))

    def test_line_count_within_screen_budget(self):
        """6 行 = title 1 行 + message 5 行，无 signature。"""
        card = _em_card()
        total = (1 if card["title"] else 0) + len(card["message"].splitlines()) \
            + (1 if card["signature"] else 0)
        self.assertEqual(total, 6)


if __name__ == "__main__":
    unittest.main()
