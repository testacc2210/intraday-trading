"""Lightweight mobile PWA backend server for NSE Intraday Algorithm."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
from datetime import datetime, time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nse_intraday.config import TradingConfig
from nse_intraday.data_provider import SyntheticReplayProvider
from nse_intraday.engine import IntradayEngine
from nse_intraday.utils import IST, is_market_day, is_within_entry_window, now_ist

logger = logging.getLogger("pwa_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class PWAHandler(BaseHTTPRequestHandler):
    """Handles static PWA delivery and /api/scan /api/status endpoints."""

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, file_path: Path, content_type: str | None = None) -> None:
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        if not content_type:
            content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            if file_path.name in {"sw.js", "manifest.json"}:
                self.send_header("Cache-Control", "no-cache")
            else:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            logger.error("Error serving %s: %s", file_path, exc)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/manifest.json":
            self._serve_file(STATIC_DIR / "manifest.json", "application/manifest+json")
        elif path in {"/sw.js", "/service-worker.js"}:
            self._serve_file(STATIC_DIR / "sw.js", "application/javascript")
        elif path.startswith("/static/"):
            rel_name = path[len("/static/"):]
            self._serve_file(STATIC_DIR / rel_name)
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/scan":
            self._handle_scan(query)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_status(self) -> None:
        now = now_ist()
        config = TradingConfig.from_env()
        is_mkt_day = is_market_day(now, config.nse_holidays_2026)
        is_open = is_mkt_day and (time(9, 15) <= now.time() < time(15, 30))
        in_entry = is_within_entry_window(now.time(), config)

        self._send_json(
            {
                "current_ist_time": now.strftime("%H:%M:%S IST"),
                "date": now.strftime("%Y-%m-%d"),
                "is_market_day": is_mkt_day,
                "is_market_open": is_open,
                "in_entry_window": in_entry,
                "entry_windows": ["09:30-10:30 IST", "13:30-14:15 IST"],
                "square_off_time": "15:10 IST",
            }
        )

    def _handle_scan(self, query: dict) -> None:
        capital = float(query.get("capital", [50000])[0])
        mode = query.get("mode", ["live"])[0]
        scenario = query.get("scenario", ["breakout_target"])[0]

        now = now_ist()
        config = TradingConfig.from_env()
        config.capital = capital

        if mode == "sim":
            # Synthetic offline simulation replay at peak entry bar (09:45 IST) with isolated state
            import copy
            import tempfile
            from nse_intraday.state_store import LocalJSONStateStore
            with tempfile.TemporaryDirectory() as tmp_dir:
                sim_config = copy.copy(config)
                sim_config.state_file_path = tmp_dir
                sim_store = LocalJSONStateStore(tmp_dir)
                provider = SyntheticReplayProvider(scenario=scenario)
                engine = IntradayEngine(config=sim_config, data_provider=provider, state_store=sim_store)
                sim_time = now.replace(hour=9, minute=45, second=0, microsecond=0)
                res = engine.run_cycle(now=sim_time)
                self._send_json(res)
                return

        # Live Scan: Check market hours first to avoid unnecessary rate limits at night
        is_mkt_day = is_market_day(now, config.nse_holidays_2026)
        if not is_mkt_day:
            self._send_json({
                "status": "NO_ACTION",
                "reason": "MARKET_HOLIDAY_OR_WEEKEND",
                "trading_date": now.strftime("%Y-%m-%d"),
                "timestamp_ist": now.isoformat(),
            })
            return

        if now.time() >= config.square_off_time:
            self._send_json({
                "status": "NO_ACTION",
                "reason": "PAST_SQUARE_OFF_TIME",
                "trading_date": now.strftime("%Y-%m-%d"),
                "timestamp_ist": now.isoformat(),
            })
            return

        if not is_within_entry_window(now.time(), config):
            self._send_json({
                "status": "NO_ACTION",
                "reason": "OUTSIDE_ENTRY_WINDOW",
                "trading_date": now.strftime("%Y-%m-%d"),
                "timestamp_ist": now.isoformat(),
            })
            return

        engine = IntradayEngine(config=config)
        res = engine.run_cycle(now=now)
        self._send_json(res)


def run_server(port: int = 8080, host: str = "0.0.0.0") -> None:
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, PWAHandler)
    logger.info("Best Intraday PWA running on http://%s:%d", host, port)
    logger.info("Mobile access: connect to the same Wi-Fi and open http://<your-laptop-ip>:%d", port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down.")
        httpd.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    run_server(port=port)
