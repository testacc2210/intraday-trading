"""Time, tick-rounding, calendar, and resilient retry utilities."""

from __future__ import annotations

import functools
import logging
import time as time_module
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

import requests

from nse_intraday.config import TradingConfig

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class RateLimitError(RuntimeError):
    """Raised when an upstream market data or broker API returns HTTP 429."""


def now_ist() -> datetime:
    """Return the current timezone-aware datetime in IST (Asia/Kolkata)."""
    return datetime.now(tz=IST)


def ensure_ist(dt: datetime) -> datetime:
    """Ensure a datetime object is timezone-aware in IST."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def round_to_nse_tick(price: float, tick_size: float = 0.05) -> float:
    """Round a price cleanly to the nearest NSE cash equity tick (default ₹0.05)."""
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    ticks = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float((ticks * d_tick).quantize(Decimal("0.01")))


def is_market_day(dt: datetime, holidays: set[str]) -> bool:
    """Return True if dt (in IST) falls on an NSE trading weekday and is not a holiday."""
    dt_ist = ensure_ist(dt)
    if dt_ist.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return dt_ist.strftime("%Y-%m-%d") not in holidays


def is_within_entry_window(t: time, config: TradingConfig) -> bool:
    """Return True only during peak institutional liquidity entry windows."""
    in_morning = config.morning_entry_start <= t <= config.morning_entry_end
    in_afternoon = config.afternoon_entry_start <= t <= config.afternoon_entry_end
    return in_morning or in_afternoon


def is_market_hours(t: time) -> bool:
    """Return True if time falls within NSE continuous equity session (09:15-15:30 IST)."""
    return time(9, 15) <= t <= time(15, 30)


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
) -> Callable[[F], F]:
    """Retry transient network timeouts, connection issues, and HTTP 429/5xx errors."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except RateLimitError as exc:
                    last_exc = exc
                except requests.exceptions.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    # Retry only rate-limits (429) or transient 5xx server errors
                    if status not in {429, 500, 502, 503, 504}:
                        raise
                    last_exc = exc
                except (
                    TimeoutError,
                    ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.RequestException,
                ) as exc:
                    last_exc = exc

                if attempt == max_attempts:
                    break

                logger.warning(
                    "Transient error in %s (attempt %d/%d): %s; retrying in %.2fs",
                    func.__name__,
                    attempt,
                    max_attempts,
                    last_exc,
                    delay,
                )
                if delay > 0:
                    time_module.sleep(delay)
                delay *= backoff_factor

            assert last_exc is not None
            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator
