"""富途集成脚本纯函数的隔离单元测试（无网络、无 OpenD、无 futu-api 依赖）。

只测可纯计算的部分：代码归一化、FIFO 已实现盈亏、持仓对账（LTI 双重持仓识别）。
拉取部分（需 OpenD）不在此覆盖。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_futu_deals as deals_mod
import fetch_futu_positions as pos_mod


def _deal(side, market, code, qty, price, time="2025-01-01 10:00:00"):
    return {"side": side, "market": market, "code": code, "qty": qty,
            "price": price, "time": time, "name": code, "currency": None}


class TestNormalizeCode(unittest.TestCase):
    def test_markets(self):
        self.assertEqual(deals_mod.normalize_futu_code("HK.00700"), ("HK", "00700"))
        self.assertEqual(deals_mod.normalize_futu_code("US.YINN"), ("US", "YINN"))
        self.assertEqual(deals_mod.normalize_futu_code("SH.600000"), ("CN", "600000"))
        self.assertEqual(deals_mod.normalize_futu_code("SZ.000001"), ("CN", "000001"))

    def test_hk_zero_pad(self):
        # 港股代码补齐到 5 位，与 明细.csv 一致
        self.assertEqual(deals_mod.normalize_futu_code("HK.2015"), ("HK", "02015"))

    def test_no_dot(self):
        self.assertEqual(deals_mod.normalize_futu_code("YINN"), (None, "YINN"))

    def test_positions_module_same(self):
        self.assertEqual(pos_mod.normalize_futu_code("HK.03690"), ("HK", "03690"))


class TestFifoRealized(unittest.TestCase):
    def test_simple_gain(self):
        r = deals_mod.fifo_realized([_deal("BUY", "US", "A", 100, 10),
                                     _deal("SELL", "US", "A", 100, 15)], [])
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]["reliable"])
        self.assertAlmostEqual(r[0]["realized_pnl"], 500.0)
        self.assertEqual(r[0]["incomplete_basis_qty"], 0)

    def test_fifo_order_multi_lot(self):
        # 买100@10, 买100@20, 卖150@30 -> (30-10)*100 + (30-20)*50 = 2500
        r = deals_mod.fifo_realized([_deal("BUY", "US", "A", 100, 10),
                                     _deal("BUY", "US", "A", 100, 20),
                                     _deal("SELL", "US", "A", 150, 30)], [])
        self.assertAlmostEqual(r[0]["realized_pnl"], 2500.0)
        self.assertTrue(r[0]["reliable"])

    def test_sell_without_buy_is_unreliable(self):
        # 窗口内无买入：不臆造成本，realized=None，标记需人工
        r = deals_mod.fifo_realized([_deal("SELL", "US", "A", 50, 20)], [])
        self.assertFalse(r[0]["reliable"])
        self.assertIsNone(r[0]["realized_pnl"])
        self.assertAlmostEqual(r[0]["incomplete_basis_qty"], 50)

    def test_partial_basis(self):
        # 买60@10, 卖100@15 -> 匹配60股 realized=(15-10)*60=300，剩40股窗口外
        r = deals_mod.fifo_realized([_deal("BUY", "US", "A", 60, 10),
                                     _deal("SELL", "US", "A", 100, 15)], [])
        self.assertFalse(r[0]["reliable"])
        self.assertAlmostEqual(r[0]["realized_pnl"], 300.0)
        self.assertAlmostEqual(r[0]["incomplete_basis_qty"], 40)
        self.assertAlmostEqual(r[0]["matched_qty"], 60)


class TestReconcile(unittest.TestCase):
    def _csv(self, account, code, market, shares):
        return {"account": account, "category": "股票", "name": code,
                "code": code, "market": market, "shares": str(shares),
                "cost_price": "10"}

    def test_match(self):
        pos = [{"futu_code": "US.A", "market": "US", "code": "A", "name": "A",
                "qty": 100.0, "cost_price": 10.0, "market_val": 1000.0, "currency": "USD"}]
        rows = [self._csv("富途", "A", "US", 100)]
        report, csv_only = pos_mod.reconcile(pos, {}, rows)
        self.assertEqual(report[0]["status"], "match")
        self.assertEqual(csv_only, [])

    def test_ambiguous_when_also_in_lti(self):
        # 同代码在 富途 + LTI 都有 -> ambiguous，--write-back 会跳过
        pos = [{"futu_code": "US.BEKE", "market": "US", "code": "BEKE", "name": "BEKE",
                "qty": 400.0, "cost_price": 61.0, "market_val": 6600.0, "currency": "USD"}]
        rows = [self._csv("富途", "BEKE", "US", 400), self._csv("LTI", "BEKE", "US", 3500)]
        report, _ = pos_mod.reconcile(pos, {}, rows)
        self.assertEqual(report[0]["status"], "ambiguous_multi_account")
        self.assertTrue(report[0]["has_lti_or_other_account"])

    def test_futu_only(self):
        pos = [{"futu_code": "US.NEW", "market": "US", "code": "NEW", "name": "NEW",
                "qty": 10.0, "cost_price": 5.0, "market_val": 50.0, "currency": "USD"}]
        report, _ = pos_mod.reconcile(pos, {}, [])
        self.assertEqual(report[0]["status"], "futu_only")

    def test_csv_only_suspected_closed(self):
        # CSV 富途 有、Futu 查不到 -> 标记疑似清仓
        rows = [self._csv("富途", "GONE", "HK", 100)]
        report, csv_only = pos_mod.reconcile([], {}, rows)
        self.assertEqual(len(csv_only), 1)
        self.assertEqual(csv_only[0]["code"], "GONE")


if __name__ == "__main__":
    unittest.main()
