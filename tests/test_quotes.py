"""zhangyu.quotes 单元测试：东财/新浪解析、降级链、缓存、交易时段门控。

全部离线：解析用夹具，网络层用 mock 替身，不发真实请求。
"""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from zhangyu.quotes import (
    GROUP_ORDER,
    INDEX_SPECS,
    Quote,
    QuoteError,
    QuoteSet,
    get_quotes,
    in_trading_window,
    load_cache,
    load_fixture,
    parse_eastmoney,
    parse_sina,
    save_cache,
)

FIXTURES = Path(__file__).parent / "fixtures"
EM_RAW = json.loads((FIXTURES / "quotes_em.json").read_text(encoding="utf-8"))
SINA_TEXT = (FIXTURES / "quotes_sina.txt").read_text(encoding="utf-8")


def _fixed_quoteset() -> QuoteSet:
    quotes = [
        Quote(key=s.key, group=s.group, label=s.label,
              price=100.0 + i, pct=0.5 - i * 0.1)
        for i, s in enumerate(INDEX_SPECS)
    ]
    return QuoteSet(quotes=quotes, source="测试")


class IndexSpecTest(unittest.TestCase):
    def test_spec_table_is_the_standard_10(self):
        keys = [s.key for s in INDEX_SPECS]
        self.assertEqual(
            keys,
            ["sh", "sz", "cyb", "hs300", "hsi", "hscei", "hstech", "dji", "ixic", "spx"],
        )

    def test_groups_cover_three_markets_in_order(self):
        groups = []
        for spec in INDEX_SPECS:
            if spec.group not in groups:
                groups.append(spec.group)
        self.assertEqual(groups, list(GROUP_ORDER))
        counts = {g: sum(1 for s in INDEX_SPECS if s.group == g) for g in GROUP_ORDER}
        self.assertEqual(counts, {"A股": 4, "港股": 3, "美股": 3})

    def test_hstech_uses_124_market(self):
        # 100.HSTECH 已死，混入批量会让东财整批 data:null（k-macao/001 PR #186）
        hstech = next(s for s in INDEX_SPECS if s.key == "hstech")
        self.assertEqual(hstech.em, "124.HSTECH")

    def test_sina_codes_present_for_all(self):
        for spec in INDEX_SPECS:
            self.assertTrue(spec.sina, spec.key)


class ParseEastmoneyTest(unittest.TestCase):
    def test_parse_full_payload(self):
        got = parse_eastmoney(EM_RAW)
        self.assertEqual(len(got), 10)
        sh = got["sh"]
        self.assertEqual(sh.label, "上证")
        self.assertEqual(sh.price, 3888.12)
        self.assertEqual(sh.pct, 0.56)
        hstech = got["hstech"]
        self.assertEqual(hstech.price, 5678.9)
        self.assertEqual(hstech.pct, -0.66)
        # 恒生国企行的 f14 是全名，靠 secid 也能对上
        self.assertEqual(got["hscei"].label, "恒生国企")

    def test_parse_dict_diff_variant(self):
        # 个别镜像把 diff 返回成 {0: {...}} 字典
        payload = {
            "data": {
                "diff": {
                    "0": {"f12": "000001", "f13": 1, "f14": "上证指数",
                          "f2": 3000.0, "f3": 1.5},
                }
            }
        }
        got = parse_eastmoney(payload)
        self.assertEqual(list(got), ["sh"])
        self.assertEqual(got["sh"].pct, 1.5)

    def test_partial_payload_returns_available_rows(self):
        payload = {"data": {"diff": [
            {"f12": "000001", "f13": 1, "f14": "上证指数", "f2": 3000.0, "f3": 1.5},
            {"f12": "HSI", "f13": 100, "f14": "恒生指数", "f2": 24000.0, "f3": -0.2},
        ]}}
        got = parse_eastmoney(payload)
        self.assertEqual(set(got), {"sh", "hsi"})

    def test_row_matched_by_f12_suffix_when_f13_missing(self):
        payload = {"data": {"diff": [
            {"f12": "HSTECH", "f14": "恒生科技", "f2": 5000.0, "f3": 0.1},
        ]}}
        got = parse_eastmoney(payload)
        self.assertEqual(list(got), ["hstech"])

    def test_row_matched_by_name_when_codes_missing(self):
        payload = {"data": {"diff": [
            {"f14": "深证成指", "f2": 13000.0, "f3": -1.0},
            {"f14": "纳斯达克综合指数", "f2": 20000.0, "f3": 2.0},
        ]}}
        got = parse_eastmoney(payload)
        self.assertEqual(got["sz"].price, 13000.0)
        self.assertEqual(got["ixic"].price, 20000.0)

    def test_dashed_price_rows_skipped(self):
        payload = {"data": {"diff": [
            {"f12": "000001", "f13": 1, "f14": "上证指数", "f2": "-", "f3": "-"},
            {"f12": "HSI", "f13": 100, "f14": "恒生指数", "f2": 24000.0, "f3": 0.1},
        ]}}
        got = parse_eastmoney(payload)
        self.assertEqual(set(got), {"hsi"})

    def test_data_null_raises(self):
        # 一个失效 secid → 整批 data:null
        for payload in ({"data": None}, {"data": {"diff": []}}, {}, None):
            with self.assertRaises(QuoteError):
                parse_eastmoney(payload)

    def test_unparseable_rows_raise(self):
        payload = {"data": {"diff": [
            {"f12": "???9999", "f13": 9, "f14": "看不懂的指数", "f2": 1.0, "f3": 0.0},
        ]}}
        with self.assertRaises(QuoteError):
            parse_eastmoney(payload)


class ParseSinaTest(unittest.TestCase):
    def test_parse_full_text(self):
        got = parse_sina(SINA_TEXT)
        self.assertEqual(len(got), 10)
        sh = got["sh"]
        self.assertEqual(sh.price, 3888.12)
        # A股：昨收 3866.46 → 涨跌幅由价格算出 (3888.12/3866.46-1)*100
        self.assertAlmostEqual(sh.pct, (3888.12 / 3866.46 - 1) * 100, places=6)
        # 港股：字段 [8] 直接是涨跌幅
        self.assertEqual(got["hsi"].price, 24567.89)
        self.assertEqual(got["hsi"].pct, 0.31)
        # 美股：[1]=价格 [2]=涨跌幅
        self.assertEqual(got["dji"].price, 47123.45)
        self.assertEqual(got["dji"].pct, -0.12)
        self.assertEqual(got["ixic"].pct, 0.25)
        self.assertEqual(got["spx"].price, 5890.12)

    def test_empty_quote_string_skipped(self):
        text = (
            'var hq_str_sh000001="";\n'
            'var hq_str_hkHSI="Hang Seng Index,恒生指数,24500.10,24492.01,'
            '24620.55,24455.80,24567.89,75.88,0.31";'
        )
        got = parse_sina(text)
        self.assertEqual(set(got), {"hsi"})

    def test_unknown_codes_ignored(self):
        text = 'var hq_str_gb_aapl="苹果,199.50,1.20,2026-11-10 06:00:00";'
        with self.assertRaises(QuoteError):
            parse_sina(text)  # 一行目标指数都没有

    def test_zero_or_negative_price_skipped(self):
        text = 'var hq_str_sh000001="上证指数,0,0,0";'
        with self.assertRaises(QuoteError):
            parse_sina(text)

    def test_malformed_text_raises(self):
        for bad in ("", "not a sina response", 'var hq_str_sh000001="x,y";'):
            with self.assertRaises(QuoteError):
                parse_sina(bad)


class FallbackChainTest(unittest.TestCase):
    """auto 源的降级链：东财挂 → 新浪 → 缓存。fetcher 全部用 mock 替身。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "quotes_cache.json"
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _quotes(*keys):
        out = {}
        for key in keys:
            spec = next(s for s in INDEX_SPECS if s.key == key)
            out[key] = Quote(key=key, group=spec.group, label=spec.label,
                             price=100.0, pct=0.5)
        return out

    def test_auto_prefers_eastmoney(self):
        full = self._quotes(*[s.key for s in INDEX_SPECS])
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        return_value=full) as em, \
             mock.patch("zhangyu.quotes.fetch_sina") as sina:
            got = get_quotes(source="auto", cache_path=self.cache)
        em.assert_called_once()
        sina.assert_not_called()
        self.assertEqual(got.source, "东方财富")
        self.assertEqual(len(got.quotes), 10)

    def test_auto_falls_back_to_sina(self):
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        side_effect=QuoteError("boom")), \
             mock.patch("zhangyu.quotes.fetch_sina",
                        return_value=self._quotes("sh", "dji")) as sina:
            got = get_quotes(source="auto", cache_path=self.cache)
        sina.assert_called_once()
        self.assertEqual(got.source, "新浪财经")
        self.assertEqual({q.key for q in got.quotes}, {"sh", "dji"})

    def test_partial_em_merged_with_sina(self):
        # 东财只回了 6 项 → 继续用新浪补齐
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        return_value=self._quotes("sh", "sz", "cyb", "hs300",
                                                  "hsi", "hscei")), \
             mock.patch("zhangyu.quotes.fetch_sina",
                        return_value=self._quotes("hstech", "dji", "ixic", "spx")):
            got = get_quotes(source="auto", cache_path=self.cache)
        self.assertEqual(len(got.quotes), 10)
        self.assertEqual(got.source, "东方财富 + 新浪财经")

    def test_full_result_saved_to_cache(self):
        full = self._quotes(*[s.key for s in INDEX_SPECS])
        with mock.patch("zhangyu.quotes.fetch_eastmoney", return_value=full), \
             mock.patch("zhangyu.quotes.fetch_sina"):
            get_quotes(source="auto", cache_path=self.cache)
        self.assertTrue(self.cache.exists())
        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(len(cached["quotes"]), 10)
        self.assertIn("saved_at", cached)

    def test_partial_result_not_saved_to_cache(self):
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        return_value=self._quotes("sh")), \
             mock.patch("zhangyu.quotes.fetch_sina", side_effect=QuoteError("x")):
            got = get_quotes(source="auto", cache_path=self.cache)
        self.assertEqual(len(got.quotes), 1)
        self.assertFalse(self.cache.exists())

    def test_all_sources_fail_then_cache_fallback(self):
        save_cache(self.cache, _fixed_quoteset(),
                   now=dt.datetime(2026, 11, 10, 8, 0, tzinfo=dt.timezone.utc))
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        side_effect=QuoteError("a")), \
             mock.patch("zhangyu.quotes.fetch_sina", side_effect=QuoteError("b")):
            got = get_quotes(source="auto", cache_path=self.cache)
        self.assertTrue(got.cached)
        self.assertIn("缓存", got.source)
        self.assertEqual(len(got.quotes), 10)

    def test_everything_fails_raises(self):
        with mock.patch("zhangyu.quotes.fetch_eastmoney",
                        side_effect=QuoteError("a")), \
             mock.patch("zhangyu.quotes.fetch_sina", side_effect=QuoteError("b")):
            with self.assertRaises(QuoteError):
                get_quotes(source="auto", cache_path=self.cache)

    def test_explicit_source_skips_others(self):
        with mock.patch("zhangyu.quotes.fetch_eastmoney") as em, \
             mock.patch("zhangyu.quotes.fetch_sina",
                        return_value=self._quotes("sh")) as sina:
            got = get_quotes(source="sina", cache_path=self.cache)
        em.assert_not_called()
        sina.assert_called_once()
        self.assertEqual(got.source, "新浪财经")

    def test_unknown_source_raises(self):
        with self.assertRaises(QuoteError):
            get_quotes(source="yahoo")


class CacheRoundTripTest(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "cache.json"
            original = _fixed_quoteset()
            save_cache(path, original,
                       now=dt.datetime(2026, 11, 10, 8, 0, tzinfo=dt.timezone.utc))
            loaded = load_cache(path)
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded.cached)
            self.assertEqual(len(loaded.quotes), 10)
            for a, b in zip(original.quotes, loaded.quotes):
                self.assertEqual(a.key, b.key)
                self.assertEqual(a.price, b.price)
                self.assertEqual(a.pct, b.pct)
            self.assertIn("缓存(", loaded.source)
            self.assertIn("11-10 08:00", loaded.source)

    def test_load_missing_or_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_cache(Path(tmp) / "nope.json"))
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_cache(bad))
            empty = Path(tmp) / "empty.json"
            empty.write_text('{"quotes": []}', encoding="utf-8")
            self.assertIsNone(load_cache(empty))


class LoadFixtureTest(unittest.TestCase):
    def test_em_raw_fixture(self):
        qs = load_fixture(str(FIXTURES / "quotes_em.json"))
        self.assertEqual(len(qs.quotes), 10)
        self.assertEqual(qs.quotes[0].key, "sh")  # 按 INDEX_SPECS 排序
        self.assertEqual(qs.quotes[0].price, 3888.12)
        self.assertEqual(qs.quotes[-1].key, "spx")

    def test_normalized_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "norm.json"
            path.write_text(json.dumps({
                "source": "fixture(手工)",
                "quotes": [
                    {"key": "dji", "price": 47000.5, "pct": -0.4},
                    {"key": "sh", "price": 3000.0, "pct": None},
                ],
            }), encoding="utf-8")
            qs = load_fixture(str(path))
        self.assertEqual([q.key for q in qs.quotes], ["sh", "dji"])  # 排序
        self.assertEqual(qs.source, "fixture(手工)")
        self.assertIsNone(qs.quotes[0].pct)

    def test_bad_fixture_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            with self.assertRaises(QuoteError):
                load_fixture(str(missing))
            bad_key = Path(tmp) / "badkey.json"
            bad_key.write_text('{"quotes": [{"key": "moon", "price": 1}]}',
                               encoding="utf-8")
            with self.assertRaises(QuoteError):
                load_fixture(str(bad_key))


class TradingWindowTest(unittest.TestCase):
    """门控逻辑：京/澳与美东的盘中窗口。"""

    BEIJING = ZoneInfo("Asia/Shanghai")
    NEWYORK = ZoneInfo("America/New_York")

    def test_beijing_weekday_mid(self):
        # 2026-11-10 是周二
        t = dt.datetime(2026, 11, 10, 10, 0, tzinfo=self.BEIJING)
        self.assertEqual(in_trading_window(t.astimezone(dt.timezone.utc)),
                         "A股/港股")

    def test_beijing_boundary_open_and_close(self):
        day = dt.datetime(2026, 11, 10, tzinfo=self.BEIJING)
        open_ok = day.replace(hour=9, minute=30)
        closed = day.replace(hour=9, minute=29)
        self.assertEqual(
            in_trading_window(open_ok.astimezone(dt.timezone.utc)), "A股/港股")
        self.assertIsNone(in_trading_window(closed.astimezone(dt.timezone.utc)))
        # 16:59 仍算（cron 延迟容忍），17:00 整点已收盘
        self.assertEqual(
            in_trading_window(day.replace(hour=16, minute=59)
                              .astimezone(dt.timezone.utc)), "A股/港股")
        self.assertIsNone(
            in_trading_window(day.replace(hour=17, minute=0)
                              .astimezone(dt.timezone.utc)))

    def test_beijing_weekend_closed(self):
        # 2026-11-07 周六 / 11-08 周日
        for d in (7, 8):
            t = dt.datetime(2026, 11, d, 10, 0, tzinfo=self.BEIJING)
            self.assertIsNone(in_trading_window(t.astimezone(dt.timezone.utc)))

    def test_newyork_weekday_mid_summer_and_winter(self):
        # 夏令时 7 月（EDT，UTC-4）：美东 10:00 → UTC 14:00
        summer = dt.datetime(2026, 7, 7, 14, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(in_trading_window(summer), "美股")
        # 冬令时 1 月（EST，UTC-5）：美东 10:00 → UTC 15:00
        winter = dt.datetime(2026, 1, 6, 15, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(in_trading_window(winter), "美股")

    def test_newyork_before_open_and_weekend(self):
        # 美东 09:00（盘前）
        t = dt.datetime(2026, 7, 7, 13, 0, tzinfo=dt.timezone.utc)
        self.assertIsNone(in_trading_window(t))
        # 美东周六 10:00 → UTC 周六 14:00
        t = dt.datetime(2026, 7, 4, 14, 0, tzinfo=dt.timezone.utc)
        self.assertIsNone(in_trading_window(t))

    def test_naive_datetime_treated_as_utc(self):
        t = dt.datetime(2026, 11, 10, 2, 0)  # UTC 周二 02:00 = 京 10:00
        self.assertEqual(in_trading_window(t), "A股/港股")


if __name__ == "__main__":
    unittest.main()
