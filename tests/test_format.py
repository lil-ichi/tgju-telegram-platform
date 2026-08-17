# -*- coding: utf-8 -*-
"""Tests for tgju_engine_format — the post formatting engine.

Run:  python -m pytest tests/ -v
"""
import pytest

import platform.tgju_engine_format as fmt


# ── esc / digits ──────────────────────────────────────────────────────────
class TestEscaping:
    def test_esc_none(self):
        assert fmt.esc(None) == ""

    def test_esc_html_special_chars(self):
        assert fmt.esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"

    def test_esc_plain_text_unchanged(self):
        assert fmt.esc("دلار ۱۸۸٬۰۰۰") == "دلار ۱۸۸٬۰۰۰"


class TestFaDigits:
    def test_fa_num_converts(self):
        assert fmt.fa_num("1234567890") == "۱۲۳۴۵۶۷۸۹۰"

    def test_fa_num_mixed(self):
        assert fmt.fa_num("price 42") == "price ۴۲"

    def test_fa_thousands_int(self):
        # Uses the ASCII comma group separator (real behavior of fa_thousands)
        assert fmt.fa_thousands("1000000") == "۱,۰۰۰,۰۰۰"

    def test_fa_thousands_decimal(self):
        assert fmt.fa_thousands("1234.5") == "۱,۲۳۴.۵۰"

    def test_fa_thousands_unparseable_passthrough(self):
        assert fmt.fa_thousands("abc") == "abc"


# ── Rial → Toman (the single most important business rule) ───────────────
class TestRialToToman:
    def test_floor_division(self):
        # 1 تومان = 10 ریال; tgju.org trims the final digit
        val, unit = fmt.convert_rial_to_toman(826480000)
        assert val == "82648000"
        assert unit == "تومان"

    def test_bank_usd_value(self):
        # 1,536,655 ریال → 153,665 تومان (floored)
        val, _ = fmt.convert_rial_to_toman("1536655")
        assert val == "153665"

    def test_string_with_commas(self):
        val, _ = fmt.convert_rial_to_toman("1,536,655")
        assert val == "153665"

    def test_unparseable_returns_empty(self):
        assert fmt.convert_rial_to_toman("not-a-number") == ("", "")

    def test_none_returns_empty(self):
        assert fmt.convert_rial_to_toman(None) == ("", "")


# ── Unit conventions (slug domain decides currency) ──────────────────────
class TestSlugUnit:
    def test_crypto_tether_is_toman(self):
        assert fmt.slug_unit("crypto-tether") == "تومان"

    def test_crypto_others_are_usd(self):
        assert fmt.slug_unit("crypto-bitcoin") == "دلار"

    def test_precious_metals_are_usd(self):
        assert fmt.slug_unit("ons") == "دلار"
        assert fmt.slug_unit("silver") == "دلار"

    def test_oil_is_usd(self):
        assert fmt.slug_unit("oil_brent") == "دلار"

    def test_world_indices_are_points(self):
        assert fmt.slug_unit("bourse_dow") == "نقطه"
        assert fmt.slug_unit("indices-ftse") == "نقطه"

    def test_domestic_currency_is_toman(self):
        assert fmt.slug_unit("price_dollar_rl") == "تومان"
        assert fmt.slug_unit("price_coin_emami") == "تومان"

    def test_channel_unit_override_wins(self):
        ch = {"unit_overrides": {"crypto-bitcoin": "تومان"}}
        assert fmt.slug_unit("crypto-bitcoin", ch) == "تومان"

    def test_unit_label(self):
        assert fmt.unit_label("تومان") == "تومان"
        assert fmt.unit_label("نقطه") == ""


# ── fmt_price ────────────────────────────────────────────────────────────
class TestFmtPrice:
    def test_global_usd_untouched(self):
        assert fmt.fmt_price("crypto-bitcoin", "1234567.89") == "۱,۲۳۴,۵۶۷.۸۹"

    def test_indices_untouched(self):
        assert fmt.fmt_price("bourse_dow", "41234.56") == "۴۱,۲۳۴.۵۶"

    def test_domestic_divided(self):
        assert fmt.fmt_price("price_dollar_rl", "1536655") == "۱۵۳,۶۶۵"

    def test_empty_price_dash(self):
        assert fmt.fmt_price("price_dollar_rl", "") == "—"


# ── Direction & chips ────────────────────────────────────────────────────
class TestDirection:
    def test_high_up(self):
        assert fmt.direction_arrow({"dir": "high"}) == "▲"

    def test_low_down(self):
        assert fmt.direction_arrow({"dir": "low"}) == "▼"

    def test_flat_empty(self):
        assert fmt.direction_arrow({"dir": ""}) == ""
        assert fmt.direction_arrow({}) == ""


class TestPlainChipLine:
    def test_full_line(self):
        row = {"name": "دلار", "price": "1536655", "dir": "high", "change_pct": "0.5"}
        line = fmt.plain_chip_line("price_dollar_rl", row, "تومان")
        assert "دلار" in line
        assert "۱۵۳,۶۶۵" in line  # divided by 10 and formatted
        assert "▲" in line
        assert "۰.۵" in line  # change % rendered in Persian digits
        assert "٪" in line

    def test_missing_price_empty(self):
        assert fmt.plain_chip_line("price_dollar_rl", {"name": "دلار"}, "تومان") == ""

    def test_dash_price_empty(self):
        row = {"name": "دلار", "price": ""}
        assert fmt.plain_chip_line("price_dollar_rl", row, "تومان") == ""
