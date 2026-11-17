"""Configuration and NSE universe/calendar definitions."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "AXISBANK",
    "KOTAKBANK",
    "INFY",
    "TCS",
    "LT",
    "TATAMOTORS",
    "TATASTEEL",
    "M&M",
    "BAJFINANCE",
    "BHARTIARTL",
    "SUNPHARMA",
)

# Official NSE Cash Equity market holidays for 2026 (YYYY-MM-DD)
DEFAULT_NSE_HOLIDAYS_2026: set[str] = {
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-20",  # Id-Ul-Fitr (Ramzan Id)
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-27",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
    "2026-12-25",  # Christmas
}


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class TradingConfig:
    """Validated runtime configuration for the NSE intraday trading engine."""

    paper_trading: bool = True
    capital: float = 50000.0
    max_risk_pct: float = 0.01
    risk_reward_ratio: float = 2.0
    max_trades_per_day: int = 1
    max_leverage: float = 1.0
    slippage_bps: float = 5.0
    square_off_time: time = time(15, 10)
    morning_entry_start: time = time(9, 30)
    morning_entry_end: time = time(10, 30)
    afternoon_entry_start: time = time(13, 30)
    afternoon_entry_end: time = time(14, 15)
    min_rvol: float = 1.5
    min_or_width_pct: float = 0.0015
    max_or_width_pct: float = 0.015
    min_body_ratio: float = 0.50
    tick_size: float = 0.05
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    state_backend: str = "local"
    state_file_path: str = "state"
    s3_bucket: str = ""
    s3_prefix: str = "state"
    broker_api_key: str = ""
    broker_access_token: str = ""
    broker_base_url: str = "https://api.kite.trade"
    request_timeout_sec: float = 10.0
    nse_holidays_2026: set[str] = field(default_factory=lambda: set(DEFAULT_NSE_HOLIDAYS_2026))

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError(f"Capital must be positive, got {self.capital}")
        # Strictly cap per-trade risk at 1% (0.01)
        if self.max_risk_pct <= 0 or self.max_risk_pct > 0.01:
            self.max_risk_pct = min(max(self.max_risk_pct, 0.0001), 0.01)
        # Strictly enforce minimum 1:1.5 Risk-to-Reward ratio
        if self.risk_reward_ratio < 1.5:
            raise ValueError(
                f"risk_reward_ratio must be >= 1.5 for positive expectancy, got {self.risk_reward_ratio}"
            )
        # Strictly enforce max 1 trade per day
        if self.max_trades_per_day != 1:
            self.max_trades_per_day = 1
        if self.max_leverage <= 0:
            raise ValueError(f"max_leverage must be positive, got {self.max_leverage}")
        if self.slippage_bps < 0:
            raise ValueError(f"slippage_bps cannot be negative, got {self.slippage_bps}")

    @classmethod
    def from_env(cls) -> TradingConfig:
        """Load configuration from environment variables with safe production defaults."""
        universe_raw = os.getenv("TRADING_UNIVERSE", "").strip()
        universe = (
            tuple(s.strip().upper() for s in universe_raw.split(",") if s.strip())
            if universe_raw
            else DEFAULT_UNIVERSE
        )
        return cls(
            paper_trading=_parse_bool(os.getenv("PAPER_TRADING"), True),
            capital=float(os.getenv("TRADING_CAPITAL", "50000")),
            max_risk_pct=float(os.getenv("MAX_RISK_PCT", "0.01")),
            risk_reward_ratio=float(os.getenv("RISK_REWARD_RATIO", "2.0")),
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "1")),
            max_leverage=float(os.getenv("MAX_LEVERAGE", "1.0")),
            slippage_bps=float(os.getenv("SLIPPAGE_BPS", "5.0")),
            min_rvol=float(os.getenv("MIN_RVOL", "1.5")),
            universe=universe,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            state_backend=os.getenv("STATE_BACKEND", "local").strip().lower(),
            state_file_path=os.getenv("STATE_FILE_PATH", "state").strip(),
            s3_bucket=os.getenv("S3_STATE_BUCKET", "").strip(),
            s3_prefix=os.getenv("S3_STATE_PREFIX", "state").strip(),
            broker_api_key=os.getenv("BROKER_API_KEY", "").strip(),
            broker_access_token=os.getenv("BROKER_ACCESS_TOKEN", "").strip(),
            broker_base_url=os.getenv("BROKER_BASE_URL", "https://api.kite.trade").strip(),
            request_timeout_sec=float(os.getenv("REQUEST_TIMEOUT_SEC", "10.0")),
        )
