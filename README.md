# NSE Intraday Algorithmic Trading System (AWS Lambda Ready)

A modular, stateless Python intraday algorithmic trading engine engineered for **Indian Cash Equities (NSE)** with strict 1% capital risk controls, realistic Indian statutory cost simulation, Telegram alerting, and direct portability to **AWS Lambda (`ap-south-1` Mumbai)**.

---

## 1. Quantitative & Architectural Design

### Chart & Data Type: Standard 5-Minute Time-Based OHLCV
- Uses **standard 5-minute OHLCV candlesticks** (`Open, High, Low, Close, Volume`).
- Avoids synthetic candle transforms (Heikin-Ashi, Renko) whose averaged prices do not exist on the NSE Central Limit Order Book (CLOB), eliminating phantom fills and miscalibrated stop-losses.
- 5-minute bars filter 1-minute bid-ask bounce noise and map 1:1 to stateless 5-minute cron / EventBridge triggers.

### Stock Universe (Top 15 Liquid NIFTY 50 F&O Stocks)
`RELIANCE`, `HDFCBANK`, `ICICIBANK`, `SBIN`, `AXISBANK`, `KOTAKBANK`, `INFY`, `TCS`, `LT`, `TATAMOTORS`, `TATASTEEL`, `M&M`, `BAJFINANCE`, `BHARTIARTL`, `SUNPHARMA`.
- Selected for deep institutional order books, minimal bid-ask spread / impact cost, and circuit-filter safety ahead of the `15:10 IST` square-off.

### Strategy: 15-Minute Opening Range Breakout (ORB-15) + Session VWAP + RVOL
1. **Opening Range (09:15–09:30 IST)**: First three closed 5m candles establish `ORH` and `ORL` (validated for width between `0.15%` and `1.50%`).
2. **Institutional Entry Windows**:
   - Morning Session: `09:30–10:30 IST`
   - Afternoon European-Overlap Session: `13:30–14:15 IST`
3. **Signal Criteria**:
   - **Long**: 5m close `> ORH`, close `> Session VWAP`, non-falling VWAP, bullish body `>= 50%` of candle range, `RVOL >= 1.5x`.
   - **Short**: 5m close `< ORL`, close `< Session VWAP`, non-rising VWAP, bearish body `>= 50%` of candle range, `RVOL >= 1.5x`.
4. **Strict Max 1 Trade Per Day**: When multiple symbols trigger simultaneously, signals are ranked by a composite **Institutional Conviction Score** (`RVOL`, candle body ratio, VWAP extension) and only the single highest-conviction symbol is traded.

### Risk Management & Position Sizing
- **Capital Risk Cap**: Strictly capped at `1%` (`0.01`) of configurable capital (`₹25,000`–`₹50,000+`).
- **Mathematical Sizing**:
  $$\text{Quantity} = \left\lfloor \frac{\text{Capital} \times \min(\text{MaxRiskPct}, 0.01)}{|\text{Entry} - \text{StopLoss}|} \right\rfloor$$
  further capped by buying power (`capital * max_leverage / entry_price`).
- **Structural Stop-Loss**: `min(Trigger Low, VWAP) - ₹0.05` (Long) or `max(Trigger High, VWAP) + ₹0.05` (Short), rounded to NSE `₹0.05` ticks.
- **Target**: Minimum `1:1.5` Risk-to-Reward (default `1:2.0`).
- **Hard Intraday Square-Off**: Mandatory exit at or after **`15:10 IST` (3:10 PM IST)**.

### Paper Trading & NSE MIS Statutory Cost Model
Toggled cleanly via `PAPER_TRADING=true/false`. Models adverse slippage (`5 bps` / `0.05%` per leg) plus full NSE MIS charges:
- **Brokerage**: `min(₹20, 0.03% of leg turnover)` per order
- **STT**: `0.025%` on sell-side turnover
- **NSE Exchange Txn**: `0.00297%` on total turnover
- **SEBI Charges**: `0.0001%` on total turnover
- **Stamp Duty**: `0.003%` on buy-side turnover
- **GST**: `18%` on `(Brokerage + Exchange Txn + SEBI Charges)`

---

## 2. Running Locally

### Run Unit & End-to-End Tests
```bash
python3 -m unittest discover -s tests -v
```

### Run Offline Full-Day Simulation (09:15 to 15:15 IST)
```bash
python3 lambda_handler.py --simulate --capital 50000 --scenario breakout_target
python3 lambda_handler.py --simulate --capital 50000 --scenario breakout_stop_loss
python3 lambda_handler.py --simulate --capital 50000 --scenario breakout_square_off
```

### Run a Single Stateless Cron Cycle
```bash
python3 lambda_handler.py --once
```

---

## 3. Deploying to AWS Lambda (`ap-south-1` Mumbai)

The engine stores daily execution state (`IDLE -> OPEN -> CLOSED` / `KILL_SWITCH`) in **Amazon S3** via `S3StateStore` and executes in a single deterministic pass per EventBridge tick (`template.yaml`), avoiding any infinite `while True` loops or persistent WebSockets.

```bash
sam build
sam deploy --region ap-south-1 --stack-name nse-intraday-algo --capabilities CAPABILITY_IAM
```
