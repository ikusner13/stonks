"""Runtime configuration, read from the environment (a .env is auto-loaded)."""

from __future__ import annotations

import os
from pathlib import Path

# Load .env once at import so both the web app and CLI see the same keys.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv ships with fastapi[standard]; fall back gracefully
    pass

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

WORKHORSE_MODEL = os.getenv("WORKHORSE_MODEL", "google/gemini-2.5-flash")
PREMIUM_MODEL = os.getenv("PREMIUM_MODEL", "anthropic/claude-sonnet-4.6")

# SQLite store for the server-side watchlist / positions.
DB_PATH = Path(os.getenv("STOCKS_DB_PATH", ROOT / "stocks.db"))
