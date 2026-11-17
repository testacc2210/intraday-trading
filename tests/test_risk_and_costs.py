"""Unit tests for RiskManager, position sizing, tick rounding, and NSE MIS statutory costs."""

from __future__ import annotations

import unittest

from nse_intraday.config import TradingConfig
from nse_intraday.costs import apply_slippage, calculate_nse_intraday_costs
from nse_intraday.models import Side
from nse_intraday.risk import RiskManager
from nse_intraday.utils import round_to_nse_tick


class TestRiskAndCosts(unittest.TestCase):
    def setUp(self) -> None:
        self.risk_manager = RiskManager()

    def test_position_sizing_exact_floor_formula(self) -> None:
        # Capital = ₹50,000, 1% risk = ₹500. Entry = 1000, SL = 987 -> diff = 13 -> floor(500/13) = 38
        qty, risk_amt = self.risk_manager.calculate_position_size(
            capital=50000.0,
            max_risk_pct=0.01,
            entry_price=1000.0,
            stop_loss=987.0,
            max_leverage=1.0,
        )
        self.assertEqual(qty, 38)
        self.assertEqual(risk_amt, 38 * 13.0)

    def test_position_sizing_25k_capital_with_buying_power_cap(self) -> None:
        # Capital = ₹25,000, 1% risk = ₹250. Entry = 1250, SL = 1245 -> risk_qty = floor(250/5) = 50
        # Buying power cap at 1x leverage = floor(25000 / 1250) = 20 -> quantity capped at 20
        qty, risk_amt = self.risk_manager.calculate_position_size(
            capital=25000.0,
            max_risk_pct=0.01,
            entry_price=1250.0,
            stop_loss=1245.0,
            max_leverage=1.0,
        )
        self.assertEqual(qty, 20)
        self.assertEqual(risk_amt, 100.0)

    def test_strict_one_percent_risk_cap_enforced(self) -> None:
        # Even if caller passes 5% risk, RiskManager & TradingConfig strictly cap at 1%
        qty, risk_amt = self.risk_manager.calculate_position_size(
            capital=50000.0,
            max_risk_pct=0.05,
            entry_price=500.0,
            stop_loss=490.0,
            max_leverage=5.0,
        )
        # 1% of 50000 = 500; 500 / 10 = 50 shares -> risk_amt = 500
        self.assertEqual(qty, 50)
        self.assertEqual(risk_amt, 500.0)

        cfg = TradingConfig(capital=50000.0, max_risk_pct=0.05, max_trades_per_day=3)
        self.assertEqual(cfg.max_risk_pct, 0.01)
        self.assertEqual(cfg.max_trades_per_day, 1)

    def test_config_rejects_rr_below_1_5(self) -> None:
        with self.assertRaises(ValueError):
            TradingConfig(risk_reward_ratio=1.2)

    def test_nse_tick_rounding(self) -> None:
        self.assertEqual(round_to_nse_tick(1285.12), 1285.10)
        self.assertEqual(round_to_nse_tick(1285.13), 1285.15)
        self.assertEqual(round_to_nse_tick(1285.174), 1285.15)
        self.assertEqual(round_to_nse_tick(1285.176), 1285.20)

    def test_adverse_slippage_direction(self) -> None:
        long_entry = apply_slippage(1000.0, Side.BUY, is_entry=True, slippage_bps=5.0)
        long_exit = apply_slippage(1000.0, Side.BUY, is_entry=False, slippage_bps=5.0)
        self.assertEqual(long_entry, 1000.50)
        self.assertEqual(long_exit, 999.50)

        short_entry = apply_slippage(1000.0, Side.SELL, is_entry=True, slippage_bps=5.0)
        short_exit = apply_slippage(1000.0, Side.SELL, is_entry=False, slippage_bps=5.0)
        self.assertEqual(short_entry, 999.50)
        self.assertEqual(short_exit, 1000.50)

    def test_nse_mis_cost_breakdown_math(self) -> None:
        # BUY 40 shares @ 1000.00 fill, exit @ 1020.00 fill
        costs = calculate_nse_intraday_costs(
            side=Side.BUY,
            quantity=40,
            entry_fill=1000.0,
            exit_fill=1020.0,
            requested_entry=999.50,
            requested_exit=1020.50,
        )
        buy_turnover = 40000.0
        sell_turnover = 40800.0
        total_turnover = 80800.0

        expected_brk = min(20.0, 0.0003 * buy_turnover) + min(20.0, 0.0003 * sell_turnover)
        expected_stt = round(0.00025 * sell_turnover, 4)
        expected_exch = round(0.0000297 * total_turnover, 4)
        expected_sebi = round(0.000001 * total_turnover, 4)
        expected_stamp = round(0.00003 * buy_turnover, 4)
        expected_gst = round(0.18 * (expected_brk + expected_exch + expected_sebi), 4)
        expected_total = round(
            expected_brk + expected_stt + expected_exch + expected_sebi + expected_stamp + expected_gst,
            4,
        )

        self.assertEqual(costs.gross_pnl, 800.0)
        self.assertEqual(costs.entry_slippage, 20.0)
        self.assertEqual(costs.exit_slippage, 20.0)
        self.assertAlmostEqual(costs.brokerage, expected_brk, places=4)
        self.assertAlmostEqual(costs.stt, expected_stt, places=4)
        self.assertAlmostEqual(costs.exchange_txn_charge, expected_exch, places=4)
        self.assertAlmostEqual(costs.sebi_charges, expected_sebi, places=4)
        self.assertAlmostEqual(costs.stamp_duty, expected_stamp, places=4)
        self.assertAlmostEqual(costs.gst, expected_gst, places=4)
        self.assertAlmostEqual(costs.total_charges, expected_total, places=4)
        self.assertAlmostEqual(costs.net_pnl, 800.0 - expected_total, places=4)


if __name__ == "__main__":
    unittest.main()
