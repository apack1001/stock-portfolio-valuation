#!/usr/bin/env python3
"""
Hermetic unit tests for the stock-portfolio-valuation skill core logic.

These tests touch NO network and NO real portfolio data — they only exercise
the pure calculation functions. Run with:

    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_prices as fp
import retirement_projection as rp


class TestParsers(unittest.TestCase):
    def test_parse_tax_rate(self):
        self.assertEqual(fp.parse_tax_rate("税率20%"), 0.20)
        self.assertEqual(fp.parse_tax_rate("未行权期权，行权价3.06，税率 7.5 %"), 0.075)
        self.assertIsNone(fp.parse_tax_rate("普通股票，无税率"))
        self.assertIsNone(fp.parse_tax_rate(""))

    def test_to_float(self):
        self.assertEqual(fp.to_float("1,234.5"), 1234.5)
        self.assertEqual(fp.to_float(10), 10.0)
        self.assertIsNone(fp.to_float("---"))
        self.assertIsNone(fp.to_float("abc"))
        self.assertIsNone(fp.to_float(""))


class TestClassifyBucket(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(fp.classify_bucket({"account": "LTI", "category": "股票"}), "lti")
        self.assertEqual(fp.classify_bucket({"category": "活期"}), "cash")
        self.assertEqual(fp.classify_bucket({"category": "存款"}), "cash")
        self.assertEqual(fp.classify_bucket({"category": "应收款"}), "restricted")
        self.assertEqual(fp.classify_bucket({"category": "社保"}), "restricted")
        self.assertEqual(fp.classify_bucket({"category": "股票"}), "invest")
        self.assertEqual(fp.classify_bucket({"category": "基金"}), "invest")

    def test_crypto_and_money_fund_are_cash(self):
        self.assertEqual(
            fp.classify_bucket({"category": "加密货币", "note": "稳定币USDT，类现金"}), "cash"
        )
        self.assertEqual(fp.classify_bucket({"category": "加密货币", "note": ""}), "invest")
        self.assertEqual(fp.classify_bucket({"category": "基金", "note": "货币基金"}), "cash")


class TestGoldDetection(unittest.TestCase):
    def test_note_marker(self):
        self.assertTrue(fp.is_gold_gram_asset({"note": "黄金克数；Au99.99 984 CNY/克"}))

    def test_no_hardcoded_code_dependency(self):
        # 去掉 002621 硬编码后，仅凭代码不再识别为黄金
        self.assertFalse(fp.is_gold_gram_asset({"note": "", "code": "002621"}))
        self.assertFalse(fp.is_gold_gram_asset({"note": "普通基金"}))


class TestFundNameMatches(unittest.TestCase):
    def test_generic_similarity(self):
        self.assertTrue(fp.fund_name_matches("天弘创业板ETF联接C", "天弘创业板ETF联接C"))
        self.assertTrue(
            fp.fund_name_matches("易方达人工智能ETF联接C", "易方达中证人工智能主题ETF联接C")
        )
        self.assertFalse(fp.fund_name_matches("天弘创业板ETF", "广发纳斯达克100"))
        self.assertFalse(fp.fund_name_matches("", "天弘"))


class TestOptionValuation(unittest.TestCase):
    def test_after_tax_intrinsic_in_the_money(self):
        # price 3.71, strike 3.06, 56000 股, 20% 税 -> (3.71-3.06)*56000*0.8 = 29120
        self.assertEqual(fp.option_after_tax_value(3.71, 3.06, 56000, 0.20), 29120.0)

    def test_out_of_the_money_is_zero(self):
        self.assertEqual(fp.option_after_tax_value(2.0, 3.06, 56000, 0.20), 0.0)


class TestSummarizeBuckets(unittest.TestCase):
    def _sample(self):
        return [
            {"account": "富途", "category": "股票", "currency": "USD",
             "market_value": 100.0, "pnl": -10.0, "cost_total": 110.0},
            {"account": "LTI", "category": "股票", "currency": "USD",
             "market_value": 200.0, "pnl": 200.0, "cost_total": 0.0},
            {"account": "招商银行", "category": "活期", "currency": "CNY",
             "market_value": 500.0, "pnl": 0.0, "cost_total": 500.0},
            {"account": "借款", "category": "应收款", "currency": "CNY",
             "market_value": 300.0, "pnl": 0.0, "cost_total": 300.0},
        ]

    def test_bucket_totals_and_lti_pnl_null(self):
        out = fp.summarize_buckets(self._sample(), usd_cny=7.0, hkd_cny=0.9)
        b = out["buckets"]
        self.assertAlmostEqual(b["invest"]["total_cny"]["mv_cny"], 700.0)   # 100*7
        self.assertAlmostEqual(b["lti"]["total_cny"]["mv_cny"], 1400.0)      # 200*7
        self.assertIsNone(b["lti"]["total_cny"]["pnl_cny"])                  # LTI 盈亏一律置空
        self.assertIsNone(b["lti"]["total_cny"]["ret_pct"])
        self.assertAlmostEqual(b["cash"]["total_cny"]["mv_cny"], 500.0)
        self.assertAlmostEqual(b["restricted"]["total_cny"]["mv_cny"], 300.0)
        self.assertAlmostEqual(out["grand_total_cny"], 2900.0)               # 700+1400+500+300


class TestSummarizeTotals(unittest.TestCase):
    def test_pnl_excludes_lti(self):
        results = [
            {"account": "富途", "currency": "USD", "market_value": 100.0, "pnl": -10.0},
            {"account": "LTI", "currency": "USD", "market_value": 200.0, "pnl": 200.0},
        ]
        t = fp.summarize_totals(results, usd_cny=7.0, hkd_cny=0.9)
        self.assertAlmostEqual(t["pnl_cny_excl_lti"], -70.0)   # 仅普通账户 -10*7
        self.assertAlmostEqual(t["usd_mv"], 300.0)             # 含 LTI 市值
        self.assertAlmostEqual(t["total_cny"], 2100.0)         # 300*7


class TestRetirementHelpers(unittest.TestCase):
    def test_age_and_month_math(self):
        self.assertEqual(rp.age_months("1990-01", date(2026, 6, 15)), 437)
        self.assertEqual(rp.format_age_months(437), "36岁5个月")
        self.assertEqual(rp.add_months("1990-01", 437), "2026-06")

    def test_base_pension_formula(self):
        p = rp.Profile(
            birth_year_month="1990-01", years_worked=10,
            annual_spending_cny=300000, annual_savings_cny=200000,
            social_avg_monthly_salary_cny_today=12000,
            historical_contribution_multiple=2.0, future_contribution_multiple=2.0,
            current_social_security_personal_account_cny=0, retirement_age=63,
        )
        res = rp.estimate_annual_pension(p, current_age=36.0, stop_age=46)
        # 社平12000 * (1+2.0)/2 * 缴费20年 * 1% = 3600/月
        self.assertAlmostEqual(res["base_monthly_pension_cny"], 3600.0, places=2)


if __name__ == "__main__":
    unittest.main()
