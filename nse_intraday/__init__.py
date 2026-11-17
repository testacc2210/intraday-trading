"""Modular, stateless NSE intraday algorithmic trading system."""

from nse_intraday.config import DEFAULT_UNIVERSE, TradingConfig
from nse_intraday.engine import IntradayEngine
from nse_intraday.models import (
    Candle,
    CostBreakdown,
    DailyState,
    ExitReason,
    Position,
    Side,
    Signal,
    TradeStatus,
)

__all__ = [
    "Candle",
    "CostBreakdown",
    "DEFAULT_UNIVERSE",
    "DailyState",
    "ExitReason",
    "IntradayEngine",
    "Position",
    "Side",
    "Signal",
    "TradeStatus",
    "TradingConfig",
]
