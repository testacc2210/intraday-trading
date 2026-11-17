"""Broker execution adapters for Paper Trading and Live NSE MIS Execution."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime

import requests

from nse_intraday.config import TradingConfig
from nse_intraday.costs import apply_slippage, calculate_nse_intraday_costs
from nse_intraday.models import ExitReason, Position, Side, Signal
from nse_intraday.utils import RateLimitError, ensure_ist, retry_with_backoff

logger = logging.getLogger(__name__)


class BrokerAdapter(ABC):
    """Abstract broker execution contract."""

    @abstractmethod
    def place_entry_order(
        self,
        signal: Signal,
        quantity: int,
        risk_amount: float,
        timestamp: datetime,
    ) -> Position:
        """Execute an intraday MIS entry order and return the opened Position."""

    @abstractmethod
    def close_position(
        self,
        position: Position,
        exit_price: float,
        exit_reason: ExitReason,
        timestamp: datetime,
    ) -> Position:
        """Square off an open MIS position and return the closed Position with cost breakdown."""


class PaperBroker(BrokerAdapter):
    """Simulates realistic NSE MIS order fills with adverse slippage and full statutory taxes."""

    def __init__(self, config: TradingConfig) -> None:
        self.config = config

    def place_entry_order(
        self,
        signal: Signal,
        quantity: int,
        risk_amount: float,
        timestamp: datetime,
    ) -> Position:
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        ts_ist = ensure_ist(timestamp)
        fill_entry = apply_slippage(
            price=signal.entry_price,
            side=signal.side,
            is_entry=True,
            slippage_bps=self.config.slippage_bps,
            tick_size=self.config.tick_size,
        )
        trade_id = f"PAPER-{ts_ist.strftime('%Y%m%d-%H%M%S')}-{signal.symbol}"

        logger.info(
            "[PAPER ENTRY] %s %s qty=%d req=%.2f fill=%.2f sl=%.2f target=%.2f",
            signal.side.value,
            signal.symbol,
            quantity,
            signal.entry_price,
            fill_entry,
            signal.stop_loss,
            signal.target,
        )

        return Position(
            trade_id=trade_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            requested_entry=signal.entry_price,
            fill_entry=fill_entry,
            stop_loss=signal.stop_loss,
            target=signal.target,
            risk_amount=risk_amount,
            entry_time=ts_ist,
        )

    def close_position(
        self,
        position: Position,
        exit_price: float,
        exit_reason: ExitReason,
        timestamp: datetime,
    ) -> Position:
        ts_ist = ensure_ist(timestamp)
        fill_exit = apply_slippage(
            price=exit_price,
            side=position.side,
            is_entry=False,
            slippage_bps=self.config.slippage_bps,
            tick_size=self.config.tick_size,
        )
        costs = calculate_nse_intraday_costs(
            side=position.side,
            quantity=position.quantity,
            entry_fill=position.fill_entry,
            exit_fill=fill_exit,
            requested_entry=position.requested_entry,
            requested_exit=exit_price,
        )

        position.exit_price = fill_exit
        position.exit_time = ts_ist
        position.exit_reason = exit_reason
        position.costs = costs

        logger.info(
            "[PAPER EXIT] %s %s qty=%d exit=%.2f reason=%s gross=%.2f charges=%.2f net=%.2f",
            position.side.value,
            position.symbol,
            position.quantity,
            fill_exit,
            exit_reason.value,
            costs.gross_pnl,
            costs.total_charges,
            costs.net_pnl,
        )
        return position


class LiveBrokerAdapter(BrokerAdapter):
    """Production-ready live NSE MIS REST adapter (Zerodha KiteConnect compatible)."""

    def __init__(self, config: TradingConfig) -> None:
        self.config = config
        self.base_url = config.broker_base_url.rstrip("/")
        self.session = requests.Session()
        if not config.broker_api_key or not config.broker_access_token:
            raise ValueError(
                "PAPER_TRADING=False requires BROKER_API_KEY and BROKER_ACCESS_TOKEN"
            )
        self.session.headers.update(
            {
                "X-Kite-Version": "3",
                "Authorization": f"token {config.broker_api_key}:{config.broker_access_token}",
            }
        )

    @retry_with_backoff(max_attempts=3, initial_delay=0.5, backoff_factor=2.0)
    def _post_order(self, payload: dict[str, str | int | float]) -> str:
        url = f"{self.base_url}/orders/regular"
        response = self.session.post(
            url,
            data=payload,
            timeout=self.config.request_timeout_sec,
        )
        if response.status_code == 429:
            raise RateLimitError("Broker order rate limit exceeded")
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Broker rejected order: {body}")
        return str(body["data"]["order_id"])

    def place_entry_order(
        self,
        signal: Signal,
        quantity: int,
        risk_amount: float,
        timestamp: datetime,
    ) -> Position:
        ts_ist = ensure_ist(timestamp)
        order_id = self._post_order(
            {
                "exchange": "NSE",
                "tradingsymbol": signal.symbol,
                "transaction_type": signal.side.value,
                "quantity": quantity,
                "product": "MIS",
                "order_type": "MARKET",
                "validity": "DAY",
                "tag": "ORB15_VWAP",
            }
        )
        return Position(
            trade_id=order_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            requested_entry=signal.entry_price,
            fill_entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            target=signal.target,
            risk_amount=risk_amount,
            entry_time=ts_ist,
        )

    def close_position(
        self,
        position: Position,
        exit_price: float,
        exit_reason: ExitReason,
        timestamp: datetime,
    ) -> Position:
        ts_ist = ensure_ist(timestamp)
        exit_tx = Side.SELL if position.side == Side.BUY else Side.BUY
        self._post_order(
            {
                "exchange": "NSE",
                "tradingsymbol": position.symbol,
                "transaction_type": exit_tx.value,
                "quantity": position.quantity,
                "product": "MIS",
                "order_type": "MARKET",
                "validity": "DAY",
                "tag": f"EXIT_{exit_reason.value[:10]}",
            }
        )
        costs = calculate_nse_intraday_costs(
            side=position.side,
            quantity=position.quantity,
            entry_fill=position.fill_entry,
            exit_fill=exit_price,
            requested_entry=position.requested_entry,
            requested_exit=exit_price,
        )
        position.exit_price = exit_price
        position.exit_time = ts_ist
        position.exit_reason = exit_reason
        position.costs = costs
        return position
