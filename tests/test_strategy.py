"""Unit tests for 5m indicators, ORB-15 + VWAP + RVOL strategy, and entry windows."""

from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta

from nse_intraday.config import TradingConfig
from nse_intraday.indicators import (
    compute_atr,
    compute_rvol,
    compute_session_vwap,
    extract_opening_range,
)
from nse_intraday.models import Candle, Side
from nse_intraday.strategy import ORB15VWAPStrategy
from nse_intraday.utils import IST, is_within_entry_window


def _make_candle(hour: int, minute: int, o: float, h: float, l: float, c: float, v: int) -> Candle:
    ts = datetime(2026, 11, 17, hour, minute, tzinfo=IST)
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


class TestStrategyAndIndicators(unittest.TestCase):
    def setUp(self) -> None:
        self.config = TradingConfig(capital=50000.0, risk_reward_ratio=2.0)
        self.strategy = ORB15VWAPStrategy()

    def test_entry_windows_peak_liquidity(self) -> None:
        self.assertFalse(is_within_entry_window(time(9, 25), self.config))
        self.assertTrue(is_within_entry_window(time(9, 30), self.config))
        self.assertTrue(is_within_entry_window(time(10, 30), self.config))
        self.assertFalse(is_within_entry_window(time(11, 15), self.config))
        self.assertTrue(is_within_entry_window(time(13, 30), self.config))
        self.assertTrue(is_within_entry_window(time(14, 15), self.config))
        self.assertFalse(is_within_entry_window(time(14, 30), self.config))

    def test_opening_range_and_indicators(self) -> None:
        bars = [
            _make_candle(9, 15, 1000.0, 1004.0, 998.0, 1002.0, 10000),
            _make_candle(9, 20, 1002.0, 1005.0, 999.0, 1001.0, 10000),
            _make_candle(9, 25, 1001.0, 1003.0, 997.0, 1002.0, 10000),
            _make_candle(9, 30, 1002.0, 1011.0, 1001.5, 1010.0, 25000),
        ]
        orh, orl = extract_opening_range(bars)  # type: ignore[misc]
        self.assertEqual(orh, 1005.0)
        self.assertEqual(orl, 997.0)
        vwap = compute_session_vwap(bars)
        self.assertEqual(len(vwap), 4)
        self.assertGreater(vwap[-1], vwap[-2])
        self.assertAlmostEqual(compute_rvol(bars, lookback=10), 2.5, places=2)
        self.assertGreater(compute_atr(bars, period=14), 0.0)

    def test_long_breakout_signal_and_sl_target_math(self) -> None:
        bars = [
            _make_candle(9, 15, 1000.0, 1003.0, 998.0, 1001.0, 10000),
            _make_candle(9, 20, 1001.0, 1004.0, 999.0, 1002.0, 10000),
            _make_candle(9, 25, 1002.0, 1003.5, 999.5, 1002.5, 10000),
            # Strong breakout above ORH=1004.0, above rising VWAP, RVOL = 3.0x
            _make_candle(9, 30, 1002.5, 1010.5, 1002.0, 1010.0, 30000),
        ]
        sig = self.strategy.evaluate_symbol("ICICIBANK", bars, self.config)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, Side.BUY)
        self.assertEqual(sig.entry_price, 1010.0)
        self.assertLess(sig.stop_loss, sig.entry_price)
        self.assertGreater(sig.target, sig.entry_price)
        self.assertGreaterEqual(sig.risk_reward, 2.0)

    def test_short_breakdown_signal(self) -> None:
        bars = [
            _make_candle(9, 15, 1000.0, 1002.0, 997.0, 999.0, 10000),
            _make_candle(9, 20, 999.0, 1001.0, 996.5, 998.0, 10000),
            _make_candle(9, 25, 998.0, 1000.0, 997.0, 997.5, 10000),
            # Strong breakdown below ORL=996.5, below falling VWAP, RVOL = 3.0x
            _make_candle(9, 30, 997.5, 998.0, 990.5, 991.0, 30000),
        ]
        sig = self.strategy.evaluate_symbol("SBIN", bars, self.config)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, Side.SELL)
        self.assertEqual(sig.entry_price, 991.0)
        self.assertGreater(sig.stop_loss, sig.entry_price)
        self.assertLess(sig.target, sig.entry_price)
        self.assertGreaterEqual(sig.risk_reward, 2.0)

    def test_rejects_low_rvol_breakout(self) -> None:
        bars = [
            _make_candle(9, 15, 1000.0, 1003.0, 998.0, 1001.0, 10000),
            _make_candle(9, 20, 1001.0, 1004.0, 999.0, 1002.0, 10000),
            _make_candle(9, 25, 1002.0, 1003.5, 999.5, 1002.5, 10000),
            # Volume 11000 -> RVOL = 1.1x < 1.5x threshold
            _make_candle(9, 30, 1002.5, 1010.5, 1002.0, 1010.0, 11000),
        ]
        sig = self.strategy.evaluate_symbol("RELIANCE", bars, self.config)
        self.assertIsNone(sig)

    def test_select_best_signal_picks_highest_conviction(self) -> None:
        base_or = [
            _make_candle(9, 15, 1000.0, 1003.0, 998.0, 1001.0, 10000),
            _make_candle(9, 20, 1001.0, 1004.0, 999.0, 1002.0, 10000),
            _make_candle(9, 25, 1002.0, 1003.5, 999.5, 1002.5, 10000),
        ]
        bars_a = base_or + [_make_candle(9, 30, 1002.5, 1008.0, 1002.0, 1006.5, 18000)]  # RVOL=1.8x
        bars_b = base_or + [_make_candle(9, 30, 1002.5, 1011.5, 1002.0, 1011.0, 42000)]  # RVOL=4.2x

        best = self.strategy.select_best_signal(
            {"RELIANCE": bars_a, "ICICIBANK": bars_b},
            self.config,
        )
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best.symbol, "ICICIBANK")


if __name__ == "__main__":
    unittest.main()
