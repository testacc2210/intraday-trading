#!/usr/bin/env python3
"""Entrypoint to launch the Best Intraday Mobile PWA server."""

import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pwa.server import run_server

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Best Intraday Mobile PWA on port {port}...")
    run_server(port=port)
