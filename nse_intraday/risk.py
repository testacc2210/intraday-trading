"""Strict risk management and mathematical position sizing."""

from __future__ import annotations

import math

from nse_intraday.config import TradingConfig
from nse_intraday.models import DailyState, Signal, TradeStatus

HARD_MAX_RISK_PCT = 0.01  # Strictly capped at 1% of total capital


class RiskManager:
    """Enforces 1% capital risk cap, floor(Risk / |Entry - SL|) sizing, and max 1 trade/day."""

    def calculate_position_size(
        self,
        capital: float,
        max_risk_pct: float,
        entry_price: float,
        stop_loss: float,
        max_leverage: float = 1.0,
    ) -> tuple[int, float]:
        """Compute share quantity and exact INR risk amount.

        Formula: Quantity = floor(Risk_Amount / abs(Entry - StopLoss)),
        capped by available buying power (capital * max_leverage).
        """
        if capital <= 0 or entry_price <= 0 or max_leverage <= 0:
            return (0, 0.0)

        effective_risk_pct = min(max(max_risk_pct, 0.0), HARD_MAX_RISK_PCT)
        if effective_risk_pct <= 0:
            return (0, 0.0)

        risk_amount_budget = capital * effective_risk_pct
        per_share_risk = abs(entry_price - stop_loss)
        if per_share_risk <= 0:
            return (0, 0.0)

        risk_qty = math.floor(risk_amount_budget / per_share_risk)
        buying_power_qty = math.floor((capital * max_leverage) / entry_price)
        quantity = max(0, min(risk_qty, buying_power_qty))
        actual_risk = round(quantity * per_share_risk, 4)
        return (quantity, actual_risk)

    def validate_new_trade(
        self,
        state: DailyState,
        signal: Signal,
        config: TradingConfig,
    ) -> tuple[bool, str, int, float]:
        """Validate daily trade limits, risk-reward ratio, and non-zero share sizing."""
        if state.status == TradeStatus.KILL_SWITCH:
            return (False, f"Kill-switch active: {state.kill_switch_reason}", 0, 0.0)
        if state.status != TradeStatus.IDLE:
            return (False, f"Daily state is {state.status.value}, not IDLE", 0, 0.0)
        if state.trades_taken >= config.max_trades_per_day or state.trades_taken >= 1:
            return (False, "Daily maximum of 1 trade already reached", 0, 0.0)
        if signal.risk_reward < 1.5:
            return (False, f"Risk-to-Reward {signal.risk_reward} below 1.5 minimum", 0, 0.0)

        quantity, actual_risk = self.calculate_position_size(
            capital=config.capital,
            max_risk_pct=config.max_risk_pct,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            max_leverage=config.max_leverage,
        )
        if quantity <= 0:
            return (
                False,
                (
                    f"Computed quantity is 0 (capital=₹{config.capital:.2f}, "
                    f"entry=₹{signal.entry_price:.2f}, sl=₹{signal.stop_loss:.2f})"
                ),
                0,
                0.0,
            )

        return (True, "OK", quantity, actual_risk)
