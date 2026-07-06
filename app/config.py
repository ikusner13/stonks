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
CACHE_DIR = Path(os.getenv("STOCKS_CACHE_DIR", ROOT / ".cache"))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

WORKHORSE_MODEL = os.getenv("WORKHORSE_MODEL", "google/gemini-3.1-flash-lite")
PREMIUM_MODEL = os.getenv("PREMIUM_MODEL", "anthropic/claude-sonnet-5")

DAILY_LLM_BUDGET_USD = float(os.getenv("DAILY_LLM_BUDGET_USD", "5"))  # 0 disables

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DAILY_JOB_HOUR_UTC = int(os.getenv("DAILY_JOB_HOUR_UTC", "21"))
SCHEDULER_TICK_SECONDS = int(os.getenv("SCHEDULER_TICK_SECONDS", "300"))
DRIFT_ALERT_ENABLED = os.getenv("DRIFT_ALERT_ENABLED", "1") == "1"
SEC_ALERTS_ENABLED = os.getenv("SEC_ALERTS_ENABLED", "1") == "1"
SEC_ALERT_HOURS = int(os.getenv("SEC_ALERT_HOURS", "6"))
SEC_LOOKBACK_DAYS = int(os.getenv("SEC_LOOKBACK_DAYS", "7"))
SEC_ALERT_FORMS = {
    s.strip()
    for s in os.getenv("SEC_ALERT_FORMS", "8-K,10-Q,10-K").split(",")
    if s.strip()
}

# SQLite store for the server-side watchlist / positions.
DB_PATH = Path(os.getenv("STOCKS_DB_PATH", ROOT / "stocks.db"))
