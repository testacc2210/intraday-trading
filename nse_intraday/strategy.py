"""15-Minute Opening Range Breakout (ORB-15) + Session VWAP + RVOL intraday strategy."""

from __future__ import annotations

from datetime import time
from typing import Optional

from nse_intraday.config import TradingConfig
from nse_intraday.indicators import (
    compute_atr,
    compute_rvol,
    compute_session_vwap,
    extract_opening_range,
)
from nse_intraday.models import Candle, Side, Signal
from nse_intraday.utils import ensure_ist, round_to_nse_tick


class ORB15VWAPStrategy:
    """Evaluates 5-minute standard OHLCV candles for institutional ORB-15 + VWAP breakouts."""

    def evaluate_symbol(
        self,
        symbol: str,
        candles: list[Candle],
        config: TradingConfig,
    ) -> Optional[Signal]:
        """Evaluate a single symbol's session candles and return a Signal if setup criteria hold."""
        if len(candles) < 4:
            return None

        ordered = sorted(candles, key=lambda c: ensure_ist(c.timestamp))
        latest = ordered[-1]
        latest_ist = ensure_ist(latest.timestamp)

        # Breakout must occur strictly after the 09:15-09:30 opening range completes
        if latest_ist.time() < time(9, 30):
            return None

        opening_range = extract_opening_range(ordered)
        if opening_range is None:
            return None

        orh, orl = opening_range
        if orl <= 0 or orh <= orl:
            return None

        or_width_pct = (orh - orl) / orl
        if not (config.min_or_width_pct <= or_width_pct <= config.max_or_width_pct):
            return None

        vwap_series = compute_session_vwap(ordered)
        vwap_curr = vwap_series[-1]
        vwap_prev = vwap_series[-2]
        rvol = compute_rvol(ordered, lookback=10)
        atr = compute_atr(ordered, period=14)

        if rvol < config.min_rvol:
            return None

        total_range = latest.total_range
        if total_range <= 0:
            return None

        body_ratio = latest.body_size / total_range
        if body_ratio < config.min_body_ratio:
            return None

        # Volatility sanity filter: avoid ultra-dead or freak spike candles
        if atr > 0 and (total_range < 0.25 * atr or total_range > 4.0 * atr):
            return None

        side: Optional[Side] = None
        if (
            latest.close > latest.open
            and latest.close > orh
            and latest.close > vwap_curr
            and vwap_curr >= vwap_prev
        ):
            side = Side.BUY
        elif (
            latest.close < latest.open
            and latest.close < orl
            and latest.close < vwap_curr
            and vwap_curr <= vwap_prev
        ):
            side = Side.SELL

        if side is None:
            return None

        entry = round_to_nse_tick(latest.close, config.tick_size)
        if side == Side.BUY:
            raw_sl = min(latest.low, vwap_curr) - config.tick_size
            stop_loss = round_to_nse_tick(raw_sl, config.tick_size)
            risk_per_share = entry - stop_loss
            if risk_per_share < config.tick_size:
                return None
            target = round_to_nse_tick(
                entry + config.risk_reward_ratio * risk_per_share,
                config.tick_size,
            )
            while (target - entry) / risk_per_share < 1.5:
                target = round_to_nse_tick(target + config.tick_size, config.tick_size)
            actual_rr = round((target - entry) / risk_per_share, 4)
        else:
            raw_sl = max(latest.high, vwap_curr) + config.tick_size
            stop_loss = round_to_nse_tick(raw_sl, config.tick_size)
            risk_per_share = stop_loss - entry
            if risk_per_share < config.tick_size:
                return None
            target = round_to_nse_tick(
                entry - config.risk_reward_ratio * risk_per_share,
                config.tick_size,
            )
            while target > config.tick_size and (entry - target) / risk_per_share < 1.5:
                target = round_to_nse_tick(target - config.tick_size, config.tick_size)
            if target <= 0:
                return None
            actual_rr = round((entry - target) / risk_per_share, 4)

        vwap_dist_norm = abs(latest.close - vwap_curr) / max(atr, total_range, 1e-6)
        conviction_score = round(
            0.45 * min(rvol, 5.0)
            + 0.30 * (body_ratio * 3.0)
            + 0.25 * (min(vwap_dist_norm, 2.0) * 1.5),
            4,
        )

        direction_label = "Long ORB-15 Breakout" if side == Side.BUY else "Short ORB-15 Breakdown"
        reason = (
            f"{direction_label} | Close={entry:.2f}, OR=[{orl:.2f}, {orh:.2f}], "
            f"VWAP={vwap_curr:.2f}, RVOL={rvol:.2f}x"
        )

        return Signal(
            symbol=symbol,
            side=side,
            entry_price=entry,
            stop_loss=stop_loss,
            target=target,
            risk_reward=actual_rr,
            conviction_score=conviction_score,
            vwap=round(vwap_curr, 2),
            rvol=round(rvol, 2),
            orh=round(orh, 2),
            orl=round(orl, 2),
            reason=reason,
            timestamp=latest_ist,
        )

    def select_best_signal(
        self,
        universe_candles: dict[str, list[Candle]],
        config: TradingConfig,
    ) -> Optional[Signal]:
        """Evaluate all symbols in the universe and return only the highest-conviction signal."""
        candidates: list[Signal] = []
        for symbol, candles in universe_candles.items():
            signal = self.evaluate_symbol(symbol, candles, config)
            if signal is not None:
                candidates.append(signal)

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.conviction_score)
