"""Stateless single-cycle intraday execution engine for cron and AWS Lambda."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from nse_intraday.broker import BrokerAdapter, LiveBrokerAdapter, PaperBroker
from nse_intraday.config import TradingConfig
from nse_intraday.data_provider import MarketDataProvider, YahooFinanceNSEProvider
from nse_intraday.models import Candle, ExitReason, Position, Side, TradeStatus
from nse_intraday.notifier import TelegramNotifier
from nse_intraday.risk import RiskManager
from nse_intraday.state_store import LocalJSONStateStore, S3StateStore, StateStore
from nse_intraday.strategy import ORB15VWAPStrategy
from nse_intraday.utils import ensure_ist, is_market_day, is_within_entry_window, now_ist

logger = logging.getLogger(__name__)


class IntradayEngine:
    """Stateless single-pass orchestrator evaluating one 5-minute market tick per invocation."""

    def __init__(
        self,
        config: Optional[TradingConfig] = None,
        data_provider: Optional[MarketDataProvider] = None,
        broker: Optional[BrokerAdapter] = None,
        state_store: Optional[StateStore] = None,
        notifier: Optional[TelegramNotifier] = None,
        strategy: Optional[ORB15VWAPStrategy] = None,
        risk_manager: Optional[RiskManager] = None,
    ) -> None:
        self.config = config or TradingConfig.from_env()
        self.data_provider = data_provider or YahooFinanceNSEProvider(
            timeout_sec=self.config.request_timeout_sec
        )
        if broker is not None:
            self.broker = broker
        elif self.config.paper_trading:
            self.broker = PaperBroker(self.config)
        else:
            self.broker = LiveBrokerAdapter(self.config)

        if state_store is not None:
            self.state_store = state_store
        elif self.config.state_backend == "s3":
            self.state_store = S3StateStore(
                bucket=self.config.s3_bucket,
                prefix=self.config.s3_prefix,
            )
        else:
            self.state_store = LocalJSONStateStore(self.config.state_file_path)

        self.notifier = notifier or TelegramNotifier(
            bot_token=self.config.telegram_bot_token,
            chat_id=self.config.telegram_chat_id,
            timeout_sec=self.config.request_timeout_sec,
        )
        self.strategy = strategy or ORB15VWAPStrategy()
        self.risk_manager = risk_manager or RiskManager()

    def _check_position_exit(
        self,
        pos: Position,
        candles: list[Candle],
        now_dt: datetime,
    ) -> Optional[tuple[float, ExitReason]]:
        """Evaluate Stop-Loss, Target, and 3:10 PM IST hard square-off on closed bars."""
        entry_dt = ensure_ist(pos.entry_time)
        post_entry_bars = [
            c
            for c in candles
            if ensure_ist(c.timestamp) + timedelta(minutes=5) > entry_dt
        ]

        for bar in post_entry_bars:
            if pos.side == Side.BUY:
                if bar.low <= pos.stop_loss:
                    return (pos.stop_loss, ExitReason.STOP_LOSS)
                if bar.high >= pos.target:
                    return (pos.target, ExitReason.TARGET)
            else:
                if bar.high >= pos.stop_loss:
                    return (pos.stop_loss, ExitReason.STOP_LOSS)
                if bar.low <= pos.target:
                    return (pos.target, ExitReason.TARGET)

        if now_dt.time() >= self.config.square_off_time:
            last_price = candles[-1].close if candles else pos.fill_entry
            return (last_price, ExitReason.TIME_SQUARE_OFF_1510)

        return None

    def _fetch_universe_candles(self, now_dt: datetime) -> dict[str, list[Candle]]:
        """Fetch 5-minute session candles for all universe symbols, tolerating isolated glitches."""
        universe_candles: dict[str, list[Candle]] = {}
        failures: list[str] = []

        for symbol in self.config.universe:
            try:
                bars = self.data_provider.fetch_intraday_5m(symbol, now_dt)
                if bars:
                    universe_candles[symbol] = bars
            except Exception as exc:
                logger.warning("Failed fetching 5m data for %s: %s", symbol, exc)
                failures.append(f"{symbol}: {exc}")

        if not universe_candles and failures:
            raise RuntimeError(
                f"Universe data fetch failed across all symbols: {'; '.join(failures[:3])}"
            )
        return universe_candles

    def run_cycle(self, now: Optional[datetime] = None) -> dict[str, Any]:
        """Execute one stateless trading cycle at `now` (defaults to current IST time)."""
        now_dt = ensure_ist(now) if now is not None else now_ist()
        trading_date = now_dt.strftime("%Y-%m-%d")

        if not is_market_day(now_dt, self.config.nse_holidays_2026):
            return {
                "status": "MARKET_HOLIDAY_OR_WEEKEND",
                "trading_date": trading_date,
                "timestamp_ist": now_dt.isoformat(),
            }

        state = self.state_store.load_state(trading_date)

        if state.status == TradeStatus.KILL_SWITCH:
            return {
                "status": "NO_ACTION",
                "reason": "KILL_SWITCH_ACTIVE",
                "kill_switch_reason": state.kill_switch_reason,
                "trading_date": trading_date,
                "timestamp_ist": now_dt.isoformat(),
            }

        if state.status == TradeStatus.CLOSED:
            return {
                "status": "NO_ACTION",
                "reason": "DAILY_TRADE_COMPLETED",
                "trades_taken": state.trades_taken,
                "trading_date": trading_date,
                "timestamp_ist": now_dt.isoformat(),
            }

        try:
            # 1. Monitor / exit active position
            if state.status == TradeStatus.OPEN and state.active_position is not None:
                pos = state.active_position
                candles = self.data_provider.fetch_intraday_5m(pos.symbol, now_dt)
                exit_decision = self._check_position_exit(pos, candles, now_dt)

                if exit_decision is not None:
                    exit_price, exit_reason = exit_decision
                    closed_pos = self.broker.close_position(
                        position=pos,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        timestamp=now_dt,
                    )
                    state.closed_positions.append(closed_pos)
                    state.active_position = None
                    state.status = TradeStatus.CLOSED
                    state.last_updated_ist = now_dt.isoformat()
                    self.state_store.save_state(state)
                    self.notifier.send_trade_exited(
                        closed_pos,
                        paper=self.config.paper_trading,
                    )
                    return {
                        "status": "EXITED",
                        "trading_date": trading_date,
                        "timestamp_ist": now_dt.isoformat(),
                        "symbol": closed_pos.symbol,
                        "exit_reason": exit_reason.value,
                        "exit_price": closed_pos.exit_price,
                        "gross_pnl": closed_pos.costs.gross_pnl if closed_pos.costs else 0.0,
                        "total_charges": closed_pos.costs.total_charges if closed_pos.costs else 0.0,
                        "net_pnl": closed_pos.costs.net_pnl if closed_pos.costs else 0.0,
                    }

                state.last_updated_ist = now_dt.isoformat()
                self.state_store.save_state(state)
                last_px = candles[-1].close if candles else pos.fill_entry
                return {
                    "status": "HOLDING",
                    "trading_date": trading_date,
                    "timestamp_ist": now_dt.isoformat(),
                    "symbol": pos.symbol,
                    "side": pos.side.value,
                    "quantity": pos.quantity,
                    "last_price": last_px,
                    "stop_loss": pos.stop_loss,
                    "target": pos.target,
                }

            # 2. Evaluate new entry only if IDLE, trades_taken == 0, and inside liquidity window
            if now_dt.time() >= self.config.square_off_time:
                return {
                    "status": "NO_ACTION",
                    "reason": "PAST_SQUARE_OFF_TIME",
                    "trading_date": trading_date,
                    "timestamp_ist": now_dt.isoformat(),
                }

            if not is_within_entry_window(now_dt.time(), self.config):
                return {
                    "status": "NO_ACTION",
                    "reason": "OUTSIDE_ENTRY_WINDOW",
                    "trading_date": trading_date,
                    "timestamp_ist": now_dt.isoformat(),
                }

            universe_candles = self._fetch_universe_candles(now_dt)
            signal = self.strategy.select_best_signal(universe_candles, self.config)
            if signal is None:
                state.last_updated_ist = now_dt.isoformat()
                self.state_store.save_state(state)
                return {
                    "status": "NO_SIGNAL",
                    "trading_date": trading_date,
                    "timestamp_ist": now_dt.isoformat(),
                    "scanned_symbols": len(universe_candles),
                }

            ok, reason, qty, risk_amt = self.risk_manager.validate_new_trade(
                state=state,
                signal=signal,
                config=self.config,
            )
            if not ok:
                return {
                    "status": "SIGNAL_REJECTED",
                    "trading_date": trading_date,
                    "timestamp_ist": now_dt.isoformat(),
                    "symbol": signal.symbol,
                    "reason": reason,
                }

            position = self.broker.place_entry_order(
                signal=signal,
                quantity=qty,
                risk_amount=risk_amt,
                timestamp=now_dt,
            )
            state.active_position = position
            state.trades_taken += 1
            state.status = TradeStatus.OPEN
            state.last_updated_ist = now_dt.isoformat()
            self.state_store.save_state(state)
            self.notifier.send_order_triggered(
                position=position,
                signal=signal,
                paper=self.config.paper_trading,
            )
            return {
                "status": "ENTERED",
                "trading_date": trading_date,
                "timestamp_ist": now_dt.isoformat(),
                "symbol": position.symbol,
                "side": position.side.value,
                "quantity": position.quantity,
                "requested_entry": position.requested_entry,
                "fill_entry": position.fill_entry,
                "stop_loss": position.stop_loss,
                "target": position.target,
                "risk_amount": position.risk_amount,
                "conviction_score": signal.conviction_score,
            }

        except Exception as exc:
            logger.exception("Critical failure in IntradayEngine; triggering kill-switch")
            state.status = TradeStatus.KILL_SWITCH
            state.kill_switch_reason = f"{type(exc).__name__}: {exc}"
            state.last_updated_ist = now_dt.isoformat()
            try:
                self.state_store.save_state(state)
            except Exception as save_exc:
                logger.error("Failed persisting kill-switch state: %s", save_exc)
            self.notifier.send_kill_switch_alert(
                error_msg=state.kill_switch_reason,
                trading_date=trading_date,
                paper=self.config.paper_trading,
                context=f"Cycle timestamp: {now_dt.isoformat()}",
            )
            return {
                "status": "KILL_SWITCH",
                "trading_date": trading_date,
                "timestamp_ist": now_dt.isoformat(),
                "error": state.kill_switch_reason,
            }
