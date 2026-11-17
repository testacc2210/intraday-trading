"""End-to-end tests for the stateless IntradayEngine, Lambda handler, and resilience."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from lambda_handler import lambda_handler
from nse_intraday.config import TradingConfig
from nse_intraday.data_provider import MarketDataProvider, SyntheticReplayProvider
from nse_intraday.engine import IntradayEngine
from nse_intraday.models import Candle, ExitReason, TradeStatus
from nse_intraday.notifier import TelegramNotifier
from nse_intraday.state_store import LocalJSONStateStore
from nse_intraday.utils import IST, RateLimitError, retry_with_backoff


class FailingProvider(MarketDataProvider):
    """Simulates a critical API/network failure for kill-switch testing."""

    def fetch_intraday_5m(self, symbol: str, as_of_ist: datetime) -> list[Candle]:
        raise RuntimeError("Upstream NSE feed unreachable")


class TestIntradayEngineE2E(unittest.TestCase):
    def test_holiday_and_weekend_short_circuit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TradingConfig(state_file_path=tmp)
            engine = IntradayEngine(
                config=cfg,
                data_provider=SyntheticReplayProvider(),
                state_store=LocalJSONStateStore(tmp),
            )
            # 2026-11-10 is Diwali Balipratipada in DEFAULT_NSE_HOLIDAYS_2026
            holiday_dt = datetime(2026, 11, 10, 9, 45, tzinfo=IST)
            res_hol = engine.run_cycle(now=holiday_dt)
            self.assertEqual(res_hol["status"], "MARKET_HOLIDAY_OR_WEEKEND")

            # 2026-11-14 is Saturday
            weekend_dt = datetime(2026, 11, 14, 10, 0, tzinfo=IST)
            res_wknd = engine.run_cycle(now=weekend_dt)
            self.assertEqual(res_wknd["status"], "MARKET_HOLIDAY_OR_WEEKEND")

    def test_stateless_target_exit_and_strict_one_trade_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TradingConfig(
                paper_trading=True,
                capital=50000.0,
                risk_reward_ratio=2.0,
                state_file_path=tmp,
            )
            provider = SyntheticReplayProvider(scenario="breakout_target")
            notifier = TelegramNotifier(console_fallback=False)

            session_day = datetime(2026, 11, 17, 9, 15, tzinfo=IST).date()
            current = datetime.combine(session_day, time(9, 15), tzinfo=IST)
            end_dt = datetime.combine(session_day, time(15, 15), tzinfo=IST)

            statuses: list[str] = []
            while current <= end_dt:
                # Re-instantiate engine & store on every tick to prove true statelessness
                fresh_engine = IntradayEngine(
                    config=cfg,
                    data_provider=provider,
                    state_store=LocalJSONStateStore(tmp),
                    notifier=notifier,
                )
                res = fresh_engine.run_cycle(now=current)
                statuses.append(res["status"])
                current += timedelta(minutes=5)

            self.assertEqual(statuses.count("ENTERED"), 1)
            self.assertEqual(statuses.count("EXITED"), 1)

            final_state = LocalJSONStateStore(tmp).load_state("2026-11-17")
            self.assertEqual(final_state.status, TradeStatus.CLOSED)
            self.assertEqual(final_state.trades_taken, 1)
            self.assertIsNone(final_state.active_position)
            self.assertEqual(len(final_state.closed_positions), 1)
            closed = final_state.closed_positions[0]
            self.assertEqual(closed.exit_reason, ExitReason.TARGET)
            self.assertIsNotNone(closed.costs)
            assert closed.costs is not None
            self.assertGreater(closed.costs.net_pnl, 0.0)
            self.assertGreater(closed.costs.total_charges, 0.0)

            # Verify both Telegram alerts fired (Order Triggered + Trade Exited)
            self.assertEqual(len(notifier.sent_messages), 2)
            self.assertIn("ORDER TRIGGERED", notifier.sent_messages[0])
            self.assertIn("TRADE EXITED", notifier.sent_messages[1])
            self.assertIn("TARGET", notifier.sent_messages[1])

    def test_stop_loss_exit_and_capped_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TradingConfig(
                paper_trading=True,
                capital=50000.0,
                state_file_path=tmp,
            )
            provider = SyntheticReplayProvider(scenario="breakout_stop_loss")
            notifier = TelegramNotifier(console_fallback=False)
            store = LocalJSONStateStore(tmp)
            engine = IntradayEngine(
                config=cfg,
                data_provider=provider,
                state_store=store,
                notifier=notifier,
            )

            session_day = datetime(2026, 11, 17, 9, 15, tzinfo=IST).date()
            current = datetime.combine(session_day, time(9, 15), tzinfo=IST)
            while current <= datetime.combine(session_day, time(10, 30), tzinfo=IST):
                engine.run_cycle(now=current)
                current += timedelta(minutes=5)

            state = store.load_state("2026-11-17")
            self.assertEqual(state.status, TradeStatus.CLOSED)
            self.assertEqual(state.trades_taken, 1)
            closed = state.closed_positions[0]
            self.assertEqual(closed.exit_reason, ExitReason.STOP_LOSS)
            self.assertLessEqual(closed.risk_amount, 500.0)  # <= 1% of 50,000
            self.assertIn("STOP_LOSS", notifier.sent_messages[1])

    def test_hard_square_off_at_1510_ist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TradingConfig(
                paper_trading=True,
                capital=50000.0,
                state_file_path=tmp,
            )
            provider = SyntheticReplayProvider(scenario="breakout_square_off")
            notifier = TelegramNotifier(console_fallback=False)
            store = LocalJSONStateStore(tmp)
            engine = IntradayEngine(
                config=cfg,
                data_provider=provider,
                state_store=store,
                notifier=notifier,
            )

            session_day = datetime(2026, 11, 17, 9, 15, tzinfo=IST).date()
            current = datetime.combine(session_day, time(9, 15), tzinfo=IST)
            exit_timestamp: datetime | None = None
            while current <= datetime.combine(session_day, time(15, 15), tzinfo=IST):
                res = engine.run_cycle(now=current)
                if res["status"] == "EXITED":
                    exit_timestamp = current
                    break
                current += timedelta(minutes=5)

            self.assertIsNotNone(exit_timestamp)
            assert exit_timestamp is not None
            self.assertEqual(exit_timestamp.time(), time(15, 10))
            state = store.load_state("2026-11-17")
            self.assertEqual(
                state.closed_positions[0].exit_reason,
                ExitReason.TIME_SQUARE_OFF_1510,
            )

    def test_kill_switch_on_critical_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = TradingConfig(paper_trading=True, state_file_path=tmp)
            notifier = TelegramNotifier(console_fallback=False)
            store = LocalJSONStateStore(tmp)
            engine = IntradayEngine(
                config=cfg,
                data_provider=FailingProvider(),
                state_store=store,
                notifier=notifier,
            )

            dt = datetime(2026, 11, 17, 9, 45, tzinfo=IST)
            res = engine.run_cycle(now=dt)
            self.assertEqual(res["status"], "KILL_SWITCH")
            self.assertEqual(len(notifier.sent_messages), 1)
            self.assertIn("KILL-SWITCH", notifier.sent_messages[0])

            # Subsequent cycle must remain halted
            res_next = engine.run_cycle(now=datetime(2026, 11, 17, 9, 50, tzinfo=IST))
            self.assertEqual(res_next["status"], "NO_ACTION")
            self.assertEqual(res_next["reason"], "KILL_SWITCH_ACTIVE")

    def test_retry_with_backoff_recovers_from_rate_limit_and_timeout(self) -> None:
        attempts = {"count": 0}

        @retry_with_backoff(max_attempts=3, initial_delay=0.0, backoff_factor=1.0)
        def flaky_call() -> str:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise TimeoutError("Read timed out")
            if attempts["count"] == 2:
                raise RateLimitError("HTTP 429")
            return "ok"

        self.assertEqual(flaky_call(), "ok")
        self.assertEqual(attempts["count"], 3)

    def test_aws_lambda_handler_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {
                    "PAPER_TRADING": "true",
                    "TRADING_CAPITAL": "50000",
                    "STATE_BACKEND": "local",
                    "STATE_FILE_PATH": str(Path(tmp) / "lambda_state"),
                },
            ):
                response = lambda_handler(
                    {
                        "use_synthetic": True,
                        "scenario": "breakout_target",
                        "simulate_now_ist": "2026-11-17T09:45:00+05:30",
                    }
                )
                self.assertEqual(response["statusCode"], 200)
                body = json.loads(response["body"])
                self.assertEqual(body["status"], "ENTERED")
                self.assertEqual(body["symbol"], "ICICIBANK")


if __name__ == "__main__":
    unittest.main()
