"""Market data providers for 5-minute standard NSE OHLCV candles."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta
from typing import Optional
from urllib.parse import quote

import requests

from nse_intraday.models import Candle
from nse_intraday.utils import (
    IST,
    RateLimitError,
    ensure_ist,
    retry_with_backoff,
    round_to_nse_tick,
)

logger = logging.getLogger(__name__)


class MarketDataProvider(ABC):
    """Abstract interface for fetching closed 5-minute standard OHLCV candles."""

    @abstractmethod
    def fetch_intraday_5m(self, symbol: str, as_of_ist: datetime) -> list[Candle]:
        """Fetch completed 5m OHLCV candles for `symbol` on `as_of_ist.date()` up to `as_of_ist`."""

    def fetch_latest_candle(self, symbol: str, as_of_ist: datetime) -> Optional[Candle]:
        """Return the latest closed 5m candle for `symbol`, or None if unavailable."""
        candles = self.fetch_intraday_5m(symbol, as_of_ist)
        return candles[-1] if candles else None


class YahooFinanceNSEProvider(MarketDataProvider):
    """Fetches real 5-minute NSE cash equity OHLCV candles from Yahoo Finance v8 API."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout_sec: float = 10.0) -> None:
        self.timeout_sec = timeout_sec
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
        )

    @staticmethod
    def _to_yahoo_symbol(symbol: str) -> str:
        clean = symbol.strip().upper()
        return clean if clean.endswith(".NS") else f"{clean}.NS"

    @retry_with_backoff(max_attempts=3, initial_delay=0.5, backoff_factor=2.0)
    def _fetch_chart_json(self, yahoo_symbol: str) -> dict:
        url = self.BASE_URL.format(symbol=quote(yahoo_symbol, safe="."))
        response = self.session.get(
            url,
            params={"interval": "5m", "range": "1d", "includePrePost": "false"},
            timeout=self.timeout_sec,
        )
        if response.status_code == 429:
            raise RateLimitError(f"Yahoo Finance rate limit hit for {yahoo_symbol}")
        response.raise_for_status()
        return response.json()

    def fetch_intraday_5m(self, symbol: str, as_of_ist: datetime) -> list[Candle]:
        """Return completed 5m candles for the target IST session date up to `as_of_ist`."""
        as_of = ensure_ist(as_of_ist)
        target_date = as_of.date()
        yahoo_symbol = self._to_yahoo_symbol(symbol)
        payload = self._fetch_chart_json(yahoo_symbol)

        chart = payload.get("chart", {})
        if chart.get("error"):
            raise RuntimeError(f"Yahoo chart API error for {yahoo_symbol}: {chart['error']}")

        results = chart.get("result") or []
        if not results:
            return []

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote_data.get("open") or []
        highs = quote_data.get("high") or []
        lows = quote_data.get("low") or []
        closes = quote_data.get("close") or []
        volumes = quote_data.get("volume") or []

        candles: list[Candle] = []
        for idx, ts in enumerate(timestamps):
            if ts is None:
                continue
            o = opens[idx] if idx < len(opens) else None
            h = highs[idx] if idx < len(highs) else None
            l = lows[idx] if idx < len(lows) else None
            c = closes[idx] if idx < len(closes) else None
            v = volumes[idx] if idx < len(volumes) else 0
            if None in (o, h, l, c):
                continue

            bar_dt = datetime.fromtimestamp(int(ts), tz=IST)
            if bar_dt.date() != target_date:
                continue
            # Only include 5m bars that have closed by `as_of`
            if bar_dt + timedelta(minutes=5) > as_of:
                continue
            if not (time(9, 15) <= bar_dt.time() < time(15, 30)):
                continue

            candles.append(
                Candle(
                    timestamp=bar_dt,
                    open=round_to_nse_tick(float(o)),
                    high=round_to_nse_tick(float(h)),
                    low=round_to_nse_tick(float(l)),
                    close=round_to_nse_tick(float(c)),
                    volume=int(v or 0),
                )
            )

        candles.sort(key=lambda bar: bar.timestamp)
        return candles


class BrokerDataProvider(MarketDataProvider):
    """Skeleton live-broker 5m candle provider (Zerodha KiteConnect historical endpoint)."""

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: Optional[dict[str, int]] = None,
        base_url: str = "https://api.kite.trade",
        timeout_sec: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.instrument_tokens = instrument_tokens or {}
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.session = requests.Session()

    @retry_with_backoff(max_attempts=3, initial_delay=0.5, backoff_factor=2.0)
    def fetch_intraday_5m(self, symbol: str, as_of_ist: datetime) -> list[Candle]:
        if not self.api_key or not self.access_token:
            raise RuntimeError("Missing BROKER_API_KEY or BROKER_ACCESS_TOKEN for BrokerDataProvider")
        token = self.instrument_tokens.get(symbol.upper())
        if token is None:
            raise KeyError(f"Instrument token not mapped for symbol {symbol}")

        as_of = ensure_ist(as_of_ist)
        session_start = datetime.combine(as_of.date(), time(9, 15), tzinfo=IST)
        url = f"{self.base_url}/instruments/historical/{token}/5minute"
        headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }
        params = {
            "from": session_start.strftime("%Y-%m-%d %H:%M:%S"),
            "to": as_of.strftime("%Y-%m-%d %H:%M:%S"),
        }
        resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout_sec)
        if resp.status_code == 429:
            raise RateLimitError(f"Broker rate limit exceeded fetching {symbol}")
        resp.raise_for_status()
        payload = resp.json()
        raw_candles = payload.get("data", {}).get("candles", [])
        candles: list[Candle] = []
        for row in raw_candles:
            bar_dt = ensure_ist(datetime.fromisoformat(row[0]))
            if bar_dt + timedelta(minutes=5) <= as_of:
                candles.append(
                    Candle(
                        timestamp=bar_dt,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=int(row[5]),
                    )
                )
        return candles


class SyntheticReplayProvider(MarketDataProvider):
    """Deterministic 5m candle replay provider for offline simulation and E2E testing."""

    BASE_PRICES: dict[str, float] = {
        "RELIANCE": 1280.0,
        "HDFCBANK": 1720.0,
        "ICICIBANK": 1290.0,
        "SBIN": 825.0,
        "AXISBANK": 1160.0,
        "KOTAKBANK": 1780.0,
        "INFY": 1850.0,
        "TCS": 4120.0,
        "LT": 3560.0,
        "TATAMOTORS": 890.0,
        "TATASTEEL": 148.0,
        "M&M": 2940.0,
        "BAJFINANCE": 6850.0,
        "BHARTIARTL": 1610.0,
        "SUNPHARMA": 1810.0,
    }

    def __init__(
        self,
        candles_by_symbol: Optional[dict[str, list[Candle]]] = None,
        scenario: str = "breakout_target",
    ) -> None:
        self._custom_candles = candles_by_symbol
        self.scenario = scenario
        self._generated_cache: dict[tuple[str, str], list[Candle]] = {}

    def fetch_intraday_5m(self, symbol: str, as_of_ist: datetime) -> list[Candle]:
        as_of = ensure_ist(as_of_ist)
        date_key = as_of.strftime("%Y-%m-%d")

        if self._custom_candles is not None:
            all_bars = self._custom_candles.get(symbol, [])
        else:
            cache_key = (date_key, symbol)
            if cache_key not in self._generated_cache:
                self._generated_cache[cache_key] = self._generate_session_bars(
                    symbol=symbol,
                    session_date=as_of.date(),
                )
            all_bars = self._generated_cache[cache_key]

        return [
            bar
            for bar in all_bars
            if ensure_ist(bar.timestamp).date() == as_of.date()
            and ensure_ist(bar.timestamp) + timedelta(minutes=5) <= as_of
        ]

    def _generate_session_bars(self, symbol: str, session_date) -> list[Candle]:
        """Generate a realistic 72-bar NSE 5m intraday session (09:15 to 15:10)."""
        base = self.BASE_PRICES.get(symbol, 1000.0)
        bars: list[Candle] = []
        current_dt = datetime.combine(session_date, time(9, 15), tzinfo=IST)
        end_dt = datetime.combine(session_date, time(15, 15), tzinfo=IST)

        is_hero = symbol == "ICICIBANK"
        is_secondary = symbol == "RELIANCE"
        price = base

        idx = 0
        while current_dt < end_dt:
            t = current_dt.time()
            # 09:15, 09:20, 09:25 -> Opening Range (~0.4% band around base)
            if idx == 0:
                o, h, l, c, vol = base, base * 1.003, base * 0.998, base * 1.001, 140000
            elif idx == 1:
                o, h, l, c, vol = base * 1.001, base * 1.004, base * 0.999, base * 1.002, 125000
            elif idx == 2:
                o, h, l, c, vol = base * 1.002, base * 1.0035, base * 0.9985, base * 1.0025, 115000
            elif is_hero and t == time(9, 40) and self.scenario in {
                "breakout_target",
                "breakout_stop_loss",
                "breakout_square_off",
            }:
                # Strong institutional long ORB-15 breakout bar with high RVOL
                o = base * 1.003
                l = base * 1.0025
                h = base * 1.0085
                c = base * 1.0080
                vol = 420000
            elif is_secondary and t == time(9, 40) and self.scenario in {
                "breakout_target",
                "breakout_square_off",
            }:
                # Weaker concurrent breakout so conviction ranking picks ICICIBANK
                o = base * 1.003
                l = base * 1.002
                h = base * 1.0065
                c = base * 1.0055
                vol = 230000
            elif is_hero and t > time(9, 40):
                if self.scenario == "breakout_target":
                    # Steadily trends above 2R target around 10:25
                    step = 0.0014 if t <= time(10, 35) else 0.0001
                    o = price
                    l = price * 0.9995
                    c = price * (1.0 + step)
                    h = c * 1.0005
                    vol = 130000
                elif self.scenario == "breakout_stop_loss":
                    # Reverses and hits stop-loss at 09:55
                    if t == time(9, 50):
                        o, h, l, c, vol = price, price * 1.0005, base * 0.996, base * 0.997, 210000
                    else:
                        o, h, l, c, vol = price, price * 1.001, price * 0.999, price, 100000
                else:
                    # "breakout_square_off": consolidates between SL and Target until 15:10
                    drift = 0.0002 if idx % 2 == 0 else -0.00015
                    o = price
                    c = max(base * 1.005, price * (1.0 + drift))
                    h = max(o, c) * 1.0008
                    l = min(o, c) * 0.9992
                    vol = 95000
            else:
                # Non-breakout chop inside Opening Range
                osc = 0.0006 if idx % 2 == 0 else -0.0006
                o = base * (1.0 + osc * 0.5)
                c = base * (1.0 + osc)
                h = max(o, c) * 1.0007
                l = min(o, c) * 0.9993
                vol = 100000

            bars.append(
                Candle(
                    timestamp=current_dt,
                    open=round_to_nse_tick(o),
                    high=round_to_nse_tick(max(o, h, c)),
                    low=round_to_nse_tick(min(o, l, c)),
                    close=round_to_nse_tick(c),
                    volume=int(vol),
                )
            )
            price = c
            current_dt += timedelta(minutes=5)
            idx += 1

        return bars
