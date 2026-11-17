"""Pure-Python technical indicators for 5-minute standard OHLCV NSE candles."""

from __future__ import annotations

from datetime import time
from typing import Optional

from nse_intraday.models import Candle
from nse_intraday.utils import ensure_ist


def compute_session_vwap(candles: list[Candle]) -> list[float]:
    """Compute cumulative session VWAP anchored to the first candle of the session."""
    vwap_values: list[float] = []
    cum_pv = 0.0
    cum_vol = 0

    for candle in candles:
        vol = max(candle.volume, 0)
        cum_pv += candle.typical_price * vol
        cum_vol += vol
        if cum_vol > 0:
            vwap_values.append(cum_pv / cum_vol)
        else:
            vwap_values.append(candle.typical_price)

    return vwap_values


def compute_atr(candles: list[Candle], period: int = 14) -> float:
    """Compute Average True Range (ATR) over the latest `period` candles."""
    if not candles:
        return 0.0

    true_ranges: list[float] = []
    prev_close: Optional[float] = None

    for candle in candles:
        if prev_close is None:
            tr = candle.high - candle.low
        else:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        true_ranges.append(max(tr, 0.0))
        prev_close = candle.close

    window = true_ranges[-period:]
    return sum(window) / len(window)


def compute_rvol(candles: list[Candle], lookback: int = 10) -> float:
    """Compute Relative Volume (RVOL) of the latest candle against the prior `lookback` bars."""
    if len(candles) < 2:
        return 1.0

    prior = candles[-(lookback + 1) : -1]
    if not prior:
        return 1.0

    avg_volume = sum(max(c.volume, 0) for c in prior) / len(prior)
    if avg_volume <= 0:
        return 0.0

    return max(candles[-1].volume, 0) / avg_volume


def extract_opening_range(candles: list[Candle]) -> Optional[tuple[float, float]]:
    """Extract (ORH, ORL) from the first three 5-minute bars (09:15, 09:20, 09:25 IST).

    Returns None if fewer than 3 bars exist in the 09:15-09:30 IST window.
    """
    or_bars = [
        c
        for c in candles
        if time(9, 15) <= ensure_ist(c.timestamp).time() < time(9, 30)
    ]
    if len(or_bars) < 3:
        return None

    orh = max(c.high for c in or_bars)
    orl = min(c.low for c in or_bars)
    return (orh, orl)
