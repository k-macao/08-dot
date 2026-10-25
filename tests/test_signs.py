import unittest

from zhangyu.signs import SIGNS, get_sign, parse_sign, random_sign


class ParseSignTest(unittest.TestCase):
    def test_parse_all_english_keys(self):
        for sign in SIGNS:
            self.assertEqual(parse_sign(sign.key).key, sign.key)
            self.assertEqual(parse_sign(sign.key.upper()).key, sign.key)

    def test_parse_chinese(self):
        self.assertEqual(parse_sign("白羊座").key, "aries")
        self.assertEqual(parse_sign("狮子座").key, "leo")
        self.assertEqual(parse_sign("天蝎座").key, "scorpio")

    def test_parse_short_and_symbol(self):
        self.assertEqual(parse_sign("水瓶").key, "aquarius")
        self.assertEqual(parse_sign("♌").key, "leo")
        self.assertEqual(parse_sign("  Gemini ").key, "gemini")

    def test_parse_aliases(self):
        self.assertEqual(parse_sign("天平座").key, "libra")
        self.assertEqual(parse_sign("魔羯座").key, "capricorn")

    def test_parse_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_sign("飞马座")
        with self.assertRaises(ValueError):
            parse_sign("")

    def test_get_sign(self):
        self.assertEqual(get_sign("pisces").cn, "双鱼座")
        with self.assertRaises(ValueError):
            get_sign("nope")

    def test_random_sign_deterministic(self):
        a = random_sign(seed="2026-10-25|zhangyu")
        b = random_sign(seed="2026-10-25|zhangyu")
        c = random_sign(seed="2026-10-26|zhangyu")
        self.assertEqual(a.key, b.key)
        # 不同日期种子应“可能”不同（不同日至少允许相同，这里仅验证可复现性）
        self.assertEqual(c.key, random_sign(seed="2026-10-26|zhangyu").key)
        self.assertIn(a.key, {s.key for s in SIGNS})


if __name__ == "__main__":
    unittest.main()
