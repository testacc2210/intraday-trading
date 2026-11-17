"""AWS Lambda (ap-south-1) entrypoint and local CLI runner/simulator."""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

from nse_intraday.config import TradingConfig
from nse_intraday.data_provider import SyntheticReplayProvider
from nse_intraday.engine import IntradayEngine
from nse_intraday.state_store import LocalJSONStateStore
from nse_intraday.utils import IST, ensure_ist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def lambda_handler(event: Optional[dict[str, Any]] = None, context: Any = None) -> dict[str, Any]:
    """AWS Lambda entrypoint triggered every 5 minutes by EventBridge in ap-south-1."""
    del context  # Unused Lambda context parameter
    event = event or {}
    config = TradingConfig.from_env()

    sim_now: Optional[datetime] = None
    if event.get("simulate_now_ist"):
        sim_now = ensure_ist(datetime.fromisoformat(str(event["simulate_now_ist"])))

    provider = (
        SyntheticReplayProvider(scenario=str(event.get("scenario", "breakout_target")))
        if event.get("use_synthetic")
        else None
    )

    engine = IntradayEngine(config=config, data_provider=provider)
    result = engine.run_cycle(now=sim_now)

    return {
        "statusCode": 200 if result.get("status") != "KILL_SWITCH" else 500,
        "body": json.dumps(result),
    }


def run_simulation(
    capital: float = 50000.0,
    scenario: str = "breakout_target",
    sim_date: str = "2026-11-17",
) -> None:
    """Replay a full 09:15-15:15 IST intraday session in 5-minute stateless cron steps."""
    session_day = datetime.strptime(sim_date, "%Y-%m-%d").date()
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_path = Path(tmp_dir) / "sim_state"
        config = TradingConfig(
            paper_trading=True,
            capital=capital,
            state_backend="local",
            state_file_path=str(state_path),
        )
        provider = SyntheticReplayProvider(scenario=scenario)
        store = LocalJSONStateStore(state_path)
        engine = IntradayEngine(config=config, data_provider=provider, state_store=store)

        print(f"=== NSE Intraday Stateless Simulation ({sim_date} | Scenario: {scenario}) ===")
        print(
            f"Mode: PAPER_TRADING={config.paper_trading} | Capital=₹{config.capital:,.2f} | "
            f"Max Risk=1% (₹{config.capital * 0.01:,.2f}) | Target R:R=1:{config.risk_reward_ratio}"
        )
        print("-" * 80)

        current = datetime.combine(session_day, time(9, 15), tzinfo=IST)
        end_dt = datetime.combine(session_day, time(15, 15), tzinfo=IST)

        while current <= end_dt:
            res = engine.run_cycle(now=current)
            status = res.get("status")
            if status not in {"NO_ACTION", "NO_SIGNAL"} or current.time() in {
                time(9, 30),
                time(10, 30),
                time(13, 30),
                time(15, 10),
            }:
                print(f"[{current.strftime('%H:%M')} IST] {json.dumps(res)}")
            current += timedelta(minutes=5)

        final_state = store.load_state(sim_date)
        print("-" * 80)
        print("Telegram Notifications Dispatched:")
        for idx, msg in enumerate(engine.notifier.sent_messages, start=1):
            print(f"\n--- Alert #{idx} ---\n{msg}")

        print("\n" + "-" * 80)
        print("Final Persisted DailyState JSON:")
        print(json.dumps(final_state.to_dict(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="NSE Intraday Algorithmic Trading CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="Run a single stateless cycle")
    group.add_argument(
        "--simulate",
        action="store_true",
        help="Simulate a full 09:15-15:15 IST session offline",
    )
    parser.add_argument(
        "--at",
        type=str,
        default=None,
        help="Optional IST ISO timestamp override for --once (e.g. 2026-11-17T09:45:00+05:30)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=50000.0,
        help="Capital in INR for --simulate (default: 50000)",
    )
    parser.add_argument(
        "--scenario",
        choices=["breakout_target", "breakout_stop_loss", "breakout_square_off"],
        default="breakout_target",
        help="Synthetic replay scenario for --simulate",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="2026-11-17",
        help="Session date YYYY-MM-DD for --simulate (must be an NSE trading day)",
    )

    args = parser.parse_args()
    if args.simulate:
        run_simulation(capital=args.capital, scenario=args.scenario, sim_date=args.date)
    else:
        config = TradingConfig.from_env()
        override_dt = ensure_ist(datetime.fromisoformat(args.at)) if args.at else None
        result = IntradayEngine(config=config).run_cycle(now=override_dt)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
