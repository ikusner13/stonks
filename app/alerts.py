"""Deterministic Discord alerts for price moves, 52-week ranges, and earnings."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any

from . import config, db
from .data.finnhub import fetch_earnings_calendar, fetch_quote
from .jobs import post_discord
from .portfolio.history import fetch_price_history
from .portfolio.holdings import list_holdings
from .schemas import Quote

logger = logging.getLogger(__name__)


def init_alerts_db() -> None:
    """Create alert state tables if needed."""
    with db.connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS price_ranges (
                symbol TEXT PRIMARY KEY,
                high REAL NOT NULL,
                low REAL NOT NULL,
                updated TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts_sent (
                kind TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (kind, dedupe_key)
            )
            """
        )


def already_sent(kind: str, key: str) -> bool:
    init_alerts_db()
    with db.connect() as c:
        row = c.execute(
            "SELECT 1 FROM alerts_sent WHERE kind = ? AND dedupe_key = ?",
            (kind, key),
        ).fetchone()
    return row is not None


def mark_sent(kind: str, key: str) -> None:
    init_alerts_db()
    with db.connect() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO alerts_sent (kind, dedupe_key, sent_at)
            VALUES (?, ?, ?)
            """,
            (kind, key, datetime.now(UTC).isoformat()),
        )


def alert_universe() -> list[str]:
    """Upper-cased, deduped, sorted union of holdings and watchlist symbols."""
    symbols = {holding.symbol.upper() for holding in list_holdings()}
    symbols.update(item.symbol.upper() for item in db.list_items())
    return sorted(symbols)


def _stored_range(symbol: str) -> tuple[float, float] | None:
    with db.connect() as c:
        row = c.execute(
            "SELECT high, low FROM price_ranges WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    return float(row["high"]), float(row["low"])


def _upsert_range(symbol: str, high: float, low: float, updated: str) -> None:
    with db.connect() as c:
        c.execute(
            """
            INSERT INTO price_ranges (symbol, high, low, updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                high = excluded.high,
                low = excluded.low,
                updated = excluded.updated
            """,
            (symbol, high, low, updated),
        )


def _update_range_bound(symbol: str, *, high: float | None = None, low: float | None = None) -> None:
    updates: list[str] = []
    params: list[float | str] = []
    if high is not None:
        updates.append("high = ?")
        params.append(high)
    if low is not None:
        updates.append("low = ?")
        params.append(low)
    if not updates:
        return
    params.append(symbol)
    with db.connect() as c:
        c.execute(f"UPDATE price_ranges SET {', '.join(updates)} WHERE symbol = ?", params)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _price_move_line(symbol: str, quote: Quote) -> str:
    return f"{symbol} {quote.change_percent:+.1f}% today ({_money(quote.price)})"


async def _refresh_ranges(symbols: list[str], today: str) -> None:
    if not symbols:
        return
    try:
        history, _excluded = await asyncio.to_thread(fetch_price_history, symbols, 365)
    except Exception:
        logger.warning("price history refresh failed; using stale price ranges", exc_info=True)
        return

    for column in history.columns:
        symbol = str(column).upper()
        closes = history[column].dropna()
        if closes.empty:
            continue
        high = float(closes.max())
        low = float(closes.min())
        if isfinite(high) and isfinite(low):
            _upsert_range(symbol, high, low, today)


async def run_price_alerts() -> dict:
    """Refresh ranges, check quotes, and post deterministic price/range alerts."""
    init_alerts_db()
    symbols = alert_universe()
    today = datetime.now(UTC).date().isoformat()
    await _refresh_ranges(symbols, today)

    pending: list[tuple[str, str, str]] = []
    for idx, symbol in enumerate(symbols):
        if idx:
            await asyncio.sleep(0.1)
        try:
            quote = await fetch_quote(symbol)
        except Exception:
            logger.warning("quote fetch failed for %s", symbol, exc_info=True)
            continue
        if quote is None:
            continue

        move_key = f"{symbol}:{today}"
        if abs(quote.change_percent) >= config.PRICE_MOVE_ALERT_PCT:
            pending.append(("price_move", move_key, _price_move_line(symbol, quote)))

        stored = _stored_range(symbol)
        if stored is None:
            continue
        high, low = stored
        if quote.price > high:
            pending.append(
                (
                    "range_high",
                    move_key,
                    f"{symbol} new 52-week high {_money(quote.price)} (prev {_money(high)})",
                )
            )
            _update_range_bound(symbol, high=quote.price)
        elif quote.price < low:
            pending.append(
                (
                    "range_low",
                    move_key,
                    f"{symbol} new 52-week low {_money(quote.price)} (prev {_money(low)})",
                )
            )
            _update_range_bound(symbol, low=quote.price)

    unsent = [(kind, key, line) for kind, key, line in pending if not already_sent(kind, key)]
    if not unsent:
        return {"alerts": 0}

    try:
        await post_discord("\n".join(line for _kind, _key, line in unsent))
    except Exception:
        logger.exception("price alerts Discord post failed")
        return {"alerts": 0}

    for kind, key, _line in unsent:
        mark_sent(kind, key)
    return {"alerts": len(unsent)}


def _entry_date(entry: dict[str, Any]) -> date | None:
    raw = entry.get("date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def run_earnings_alerts() -> dict:
    """Post deterministic earnings-calendar alerts for the configured window."""
    init_alerts_db()
    symbols = alert_universe()
    today = datetime.now(UTC).date()
    to_date = today + timedelta(days=config.EARNINGS_ALERT_DAYS)

    pending: list[tuple[str, str, str]] = []
    for idx, symbol in enumerate(symbols):
        if idx:
            await asyncio.sleep(0.1)
        try:
            entries = await fetch_earnings_calendar(symbol, today, to_date)
        except Exception:
            logger.warning("earnings calendar fetch failed for %s", symbol, exc_info=True)
            continue
        if not entries:
            continue
        for entry in entries:
            entry_date = _entry_date(entry)
            if entry_date is None or entry_date < today or entry_date > to_date:
                continue
            key = f"{symbol}:{entry_date.isoformat()}"
            days = (entry_date - today).days
            pending.append(
                (
                    "earnings",
                    key,
                    f"{symbol} earnings {entry_date.isoformat()} (in {days}d)",
                )
            )

    unsent = [(kind, key, line) for kind, key, line in pending if not already_sent(kind, key)]
    if not unsent:
        return {"alerts": 0}

    try:
        await post_discord("\n".join(line for _kind, _key, line in unsent))
    except Exception:
        logger.exception("earnings alerts Discord post failed")
        return {"alerts": 0}

    for kind, key, _line in unsent:
        mark_sent(kind, key)
    return {"alerts": len(unsent)}
