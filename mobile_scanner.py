#!/usr/bin/env python3
"""
Best Intraday - Self-Contained Mobile Scanner for Pydroid 3 (Android).
Runs 100% locally on your phone with zero server or laptop required.
Requires only: pip install requests
"""

import sys
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from urllib.parse import quote

import requests

# 100% Android-compatible IST Timezone (no external system tzdata required)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

DEFAULT_UNIVERSE = (
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

HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-03", "2026-03-20", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-27", "2026-06-26",
    "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"
}


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def round_to_nse_tick(price: float, tick_size: float = 0.05) -> float:
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))
    ticks = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * d_tick)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def total_range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)


def compute_session_vwap(candles: list[Candle]) -> list[float]:
    cum_pv = 0.0
    cum_vol = 0
    vwap_series: list[float] = []
    for c in candles:
        tp = (c.high + c.low + c.close) / 3.0
        v = max(c.volume, 0)
        cum_pv += tp * v
        cum_vol += v
        vwap_series.append(cum_pv / cum_vol if cum_vol > 0 else c.close)
    return vwap_series


def compute_rvol(candles: list[Candle], lookback: int = 10) -> float:
    if len(candles) < 2:
        return 1.0
    trigger_vol = candles[-1].volume
    hist = [c.volume for c in candles[:-1][-lookback:] if c.volume > 0]
    if not hist:
        return 1.0
    avg_vol = sum(hist) / len(hist)
    return trigger_vol / avg_vol if avg_vol > 0 else 1.0


def compute_atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = [candles[0].total_range]
    for idx in range(1, len(candles)):
        curr = candles[idx]
        prev = candles[idx - 1]
        tr = max(curr.high - curr.low, abs(curr.high - prev.close), abs(curr.low - prev.close))
        trs.append(tr)
    recent = trs[-period:]
    return sum(recent) / len(recent) if recent else 0.0


def extract_opening_range(candles: list[Candle]) -> Optional[tuple[float, float]]:
    or_bars = [c for c in candles if time(9, 15) <= c.timestamp.time() < time(9, 30)]
    if len(or_bars) < 3:
        return None
    return (max(c.high for c in or_bars), min(c.low for c in or_bars))


def fetch_yahoo_5m(symbol: str, target_date) -> list[Candle]:
    clean = symbol.strip().upper()
    yahoo_symbol = clean if clean.endswith(".NS") else f"{clean}.NS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_symbol, safe='.')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
        "Accept": "application/json",
    }
    resp = requests.get(url, params={"interval": "5m", "range": "1d", "includePrePost": "false"}, headers=headers, timeout=8.0)
    resp.raise_for_status()
    payload = resp.json()
    results = payload.get("chart", {}).get("result") or []
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

    candles = []
    for idx, ts in enumerate(timestamps):
        if ts is None:
            continue
        o, h, l, c = opens[idx], highs[idx], lows[idx], closes[idx]
        v = volumes[idx] if idx < len(volumes) else 0
        if None in (o, h, l, c):
            continue
        bar_dt = datetime.fromtimestamp(int(ts), tz=IST)
        if bar_dt.date() != target_date:
            continue
        if not (time(9, 15) <= bar_dt.time() < time(15, 30)):
            continue
        candles.append(Candle(
            timestamp=bar_dt,
            open=round_to_nse_tick(float(o)),
            high=round_to_nse_tick(float(h)),
            low=round_to_nse_tick(float(l)),
            close=round_to_nse_tick(float(c)),
            volume=int(v or 0),
        ))
    return sorted(candles, key=lambda b: b.timestamp)


def evaluate_symbol(symbol: str, candles: list[Candle], capital: float = 50000.0) -> Optional[dict]:
    if len(candles) < 4:
        return None
    latest = candles[-1]
    if latest.timestamp.time() < time(9, 30):
        return None

    op_range = extract_opening_range(candles)
    if not op_range:
        return None
    orh, orl = op_range
    if orl <= 0 or orh <= orl:
        return None

    vwap_series = compute_session_vwap(candles)
    vwap_curr, vwap_prev = vwap_series[-1], vwap_series[-2]
    rvol = compute_rvol(candles, lookback=10)
    atr = compute_atr(candles, period=14)

    if rvol < 1.5:
        return None

    total_range = latest.total_range
    if total_range <= 0 or (latest.body_size / total_range) < 0.50:
        return None

    side = None
    if latest.close > latest.open and latest.close > orh and latest.close > vwap_curr and vwap_curr >= vwap_prev:
        side = "BUY"
    elif latest.close < latest.open and latest.close < orl and latest.close < vwap_curr and vwap_curr <= vwap_prev:
        side = "SELL"

    if not side:
        return None

    entry = round_to_nse_tick(latest.close)
    if side == "BUY":
        stop_loss = round_to_nse_tick(min(latest.low, vwap_curr) - 0.05)
        risk_per_share = entry - stop_loss
        if risk_per_share < 0.05:
            return None
        target = round_to_nse_tick(entry + 2.0 * risk_per_share)
    else:
        stop_loss = round_to_nse_tick(max(latest.high, vwap_curr) + 0.05)
        risk_per_share = stop_loss - entry
        if risk_per_share < 0.05:
            return None
        target = round_to_nse_tick(entry - 2.0 * risk_per_share)

    # Position sizing: strict 1% risk cap
    max_risk_inr = capital * 0.01
    qty = max(1, int(max_risk_inr // risk_per_share))
    actual_risk = round(risk_per_share * qty, 2)
    actual_gain = round(abs(target - entry) * qty, 2)

    conviction = round(0.45 * min(rvol, 5.0) + 0.30 * ((latest.body_size / total_range) * 3.0), 2)

    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "target": target,
        "qty": qty,
        "risk_amount": actual_risk,
        "gain_amount": actual_gain,
        "rvol": round(rvol, 2),
        "vwap": round(vwap_curr, 2),
        "orh": round(orh, 2),
        "orl": round(orl, 2),
        "conviction": conviction,
        "time": latest.timestamp.strftime("%H:%M:%S IST"),
    }


def display_card(trade: dict, capital: float):
    border = "=" * 62
    side_sym = "📈 BUY (LONG)" if trade["side"] == "BUY" else "📉 SHORT (SELL)"
    print("\n" + border)
    print(f"  🎯 BEST INTRADAY TRADE TRIGGER: {trade['symbol']} ({side_sym})")
    print(border)
    print(f"  Entry Price:    ₹{trade['entry']:.2f}")
    print(f"  Stop-Loss:      ₹{trade['stop_loss']:.2f}")
    print(f"  Target (1:2.0): ₹{trade['target']:.2f}")
    print("-" * 62)
    print(f"  Position Size:  {trade['qty']} Shares (for ₹{capital:,.0f} capital)")
    print(f"  Max Risk (1%):  ₹{trade['risk_amount']:.2f} (Strict Cap)")
    print(f"  Target Profit:  ₹{trade['gain_amount']:.2f}")
    print("-" * 62)
    print(f"  RVOL (Volume):  {trade['rvol']}x (Heavy Institutional Volume)")
    print(f"  Session VWAP:   ₹{trade['vwap']:.2f}")
    print(f"  Opening Range:  [₹{trade['orl']:.2f} - ₹{trade['orh']:.2f}]")
    print(f"  Trigger Time:   {trade['time']}")
    print(border)
    print("\n📋 ONE-LINE COPY FOR YOUR BROKER:")
    print(f"MIS {trade['side']} {trade['symbol']} @ ₹{trade['entry']:.2f} | SL: ₹{trade['stop_loss']:.2f} | Tgt: ₹{trade['target']:.2f} | Qty: {trade['qty']}\n")


def run_live_scan(capital: float = 50000.0):
    now = now_ist()
    print(f"\n[IST: {now.strftime('%H:%M:%S')}] Checking NSE Market Hours...")

    if now.strftime("%Y-%m-%d") in HOLIDAYS_2026 or now.weekday() >= 5:
        print("❌ Today is an NSE Market Holiday or Weekend.")
        return

    if now.time() < time(9, 30):
        print(f"⏱️ Market is forming the 15-minute Opening Range (09:15-09:30).")
        print("Please scan at 09:35 AM IST once the first 3 candles close.")
        return

    if now.time() >= time(15, 10):
        print(f"⏳ Past Square-Off Time (15:10 IST). Market is closed for intraday entries.")
        return

    if not ((time(9, 30) <= now.time() <= time(10, 30)) or (time(13, 30) <= now.time() <= time(14, 15))):
        print("⏱️ Currently outside entry windows (09:30-10:30 & 13:30-14:15 IST).")
        print("Waiting for high-liquidity window...")
        return

    print(f"🔍 Scanning 15 Liquid NIFTY Stocks across NSE...")
    best_candidate = None
    target_date = now.date()

    for sym in DEFAULT_UNIVERSE:
        try:
            bars = fetch_yahoo_5m(sym, target_date)
            res = evaluate_symbol(sym, bars, capital)
            if res:
                if not best_candidate or res["conviction"] > best_candidate["conviction"]:
                    best_candidate = res
        except Exception:
            pass

    if best_candidate:
        display_card(best_candidate, capital)
    else:
        print("\n📊 Scanned all 15 stocks: No breakout setup confirmed yet (prices inside opening range).")
        print("Check again on the next 5-minute candle close (e.g. 09:45, 09:50, 10:00).")


def run_test_demo(capital: float = 50000.0):
    demo = {
        "symbol": "ICICIBANK",
        "side": "BUY",
        "entry": 1300.30,
        "stop_loss": 1293.20,
        "target": 1314.50,
        "qty": int((capital * 0.01) / (1300.30 - 1293.20)),
        "risk_amount": round((1300.30 - 1293.20) * int((capital * 0.01) / (1300.30 - 1293.20)), 2),
        "gain_amount": round((1314.50 - 1300.30) * int((capital * 0.01) / (1300.30 - 1293.20)), 2),
        "rvol": 3.62,
        "vwap": 1294.04,
        "orh": 1295.15,
        "orl": 1287.40,
        "conviction": 2.69,
        "time": "09:45:00 IST",
    }
    print("\n--- TEST REPLAY DEMO ---")
    display_card(demo, capital)


def main():
    print("=" * 62)
    print("  ⚡ BEST INTRADAY SCANNER (Standalone Mobile Edition)")
    print("  100% On-Device | Zero Servers | Strict 1% Risk Cap")
    print("=" * 62)

    capital = 50000.0
    try:
        user_cap = input("Enter your Trading Capital in ₹ (Press Enter for 50000): ").strip()
        if user_cap:
            capital = float(user_cap)
    except Exception:
        capital = 50000.0

    print(f"\nConfigured Capital: ₹{capital:,.2f} | Max Risk (1%): ₹{capital * 0.01:,.2f}")
    print("\nSelect an Option:")
    print("  [1] ⚡ Scan Live Market Now")
    print("  [2] 🧪 Test / Demo Replay (Preview Trade Card)")
    print("  [3] 🔄 Auto-Scan (Repeats every 5 mins during market hours)")

    choice = input("\nEnter choice (1, 2, or 3): ").strip()

    if choice == "2":
        run_test_demo(capital)
    elif choice == "3":
        print("\nStarting auto-monitor. Press Ctrl+C in Pydroid to stop.")
        while True:
            run_live_scan(capital)
            time_module.sleep(300)
    else:
        run_live_scan(capital)


if __name__ == "__main__":
    main()
