"""Real-time Telegram alerting for order triggers, exits, and kill-switch events."""

from __future__ import annotations

import logging
from typing import Optional

import requests

from nse_intraday.models import Position, Signal
from nse_intraday.utils import RateLimitError, retry_with_backoff

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Dispatches structured alerts to Telegram Bot API with resilient retry & fallback."""

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        timeout_sec: float = 10.0,
        console_fallback: bool = True,
    ) -> None:
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()
        self.timeout_sec = timeout_sec
        self.console_fallback = console_fallback
        self.session = requests.Session()
        self.sent_messages: list[str] = []

    @property
    def is_configured(self) -> bool:
        """Return True if both bot token and chat ID are configured."""
        return bool(self.bot_token and self.chat_id)

    @retry_with_backoff(max_attempts=3, initial_delay=0.5, backoff_factor=2.0)
    def _post_telegram(self, text: str) -> None:
        url = self.API_URL.format(token=self.bot_token)
        response = self.session.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=self.timeout_sec,
        )
        if response.status_code == 429:
            raise RateLimitError("Telegram API rate limit exceeded")
        response.raise_for_status()

    def _dispatch(self, message: str) -> bool:
        self.sent_messages.append(message)
        if not self.is_configured:
            if self.console_fallback:
                logger.info("[TELEGRAM_FALLBACK]\n%s", message)
            return False

        try:
            self._post_telegram(message)
            return True
        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)
            return False

    def send_order_triggered(
        self,
        position: Position,
        signal: Signal,
        paper: bool,
    ) -> bool:
        """Alert 1: Order Triggered (Symbol, Side, Qty, Entry, SL, Target)."""
        mode = "PAPER" if paper else "LIVE"
        msg = (
            f"<b>[{mode}] ORDER TRIGGERED</b>\n"
            f"Symbol: <b>{position.symbol}</b>\n"
            f"Side: <b>{position.side.value}</b>\n"
            f"Qty: <b>{position.quantity}</b>\n"
            f"Entry: ₹{position.fill_entry:.2f} (Signal: ₹{position.requested_entry:.2f})\n"
            f"Stop-Loss: ₹{position.stop_loss:.2f}\n"
            f"Target: ₹{position.target:.2f} (R:R 1:{signal.risk_reward:.2f})\n"
            f"Max Risk: ₹{position.risk_amount:.2f}\n"
            f"Setup: {signal.reason}\n"
            f"Time (IST): {position.entry_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return self._dispatch(msg)

    def send_trade_exited(self, position: Position, paper: bool) -> bool:
        """Alert 2: Trade Exited (P&L, exit reason, and charges)."""
        mode = "PAPER" if paper else "LIVE"
        costs = position.costs
        gross_pnl = costs.gross_pnl if costs else 0.0
        total_charges = costs.total_charges if costs else 0.0
        net_pnl = costs.net_pnl if costs else 0.0
        exit_reason = position.exit_reason.value if position.exit_reason else "UNKNOWN"
        exit_time_str = (
            position.exit_time.strftime("%Y-%m-%d %H:%M:%S")
            if position.exit_time
            else "N/A"
        )
        exit_px = position.exit_price if position.exit_price is not None else 0.0

        charges_line = (
            f"Charges: ₹{total_charges:.2f} "
            f"(Brk: ₹{costs.brokerage:.2f}, STT: ₹{costs.stt:.2f}, "
            f"Taxes/Fees: ₹{(total_charges - costs.brokerage - costs.stt):.2f})"
            if costs
            else "Charges: ₹0.00"
        )

        msg = (
            f"<b>[{mode}] TRADE EXITED</b>\n"
            f"Symbol: <b>{position.symbol}</b> ({position.side.value} x {position.quantity})\n"
            f"Entry: ₹{position.fill_entry:.2f} -> Exit: ₹{exit_px:.2f}\n"
            f"Exit Reason: <b>{exit_reason}</b>\n"
            f"Gross P&L: ₹{gross_pnl:+.2f}\n"
            f"{charges_line}\n"
            f"Net P&L: <b>₹{net_pnl:+.2f}</b>\n"
            f"Time (IST): {exit_time_str}"
        )
        return self._dispatch(msg)

    def send_kill_switch_alert(
        self,
        error_msg: str,
        trading_date: str,
        paper: bool,
        context: Optional[str] = None,
    ) -> bool:
        """Alert 3: Kill-Switch / Critical Errors."""
        mode = "PAPER" if paper else "LIVE"
        extra = f"\nContext: {context}" if context else ""
        msg = (
            f"<b>[{mode}] KILL-SWITCH / CRITICAL ERROR</b>\n"
            f"Date: {trading_date}\n"
            f"Error: {error_msg}{extra}\n"
            f"Action: Halting further automated entries for today."
        )
        return self._dispatch(msg)
