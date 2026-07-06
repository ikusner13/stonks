"""Server-side watchlist store (SQLite). Single-user personal tool — no auth.

Replaces the original browser-localStorage watchlist so the list survives
across devices. One table, two columns: a symbol and an optional dollar position
used by portfolio API responses.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from math import isfinite
from typing import Iterator

from pydantic import BaseModel

from . import config


class WatchItem(BaseModel):
    symbol: str
    value: float | None = None


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                value  REAL,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )


def get_setting(key: str) -> str | None:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_setting(key: str, value: str) -> None:
    with connect() as c:
        c.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_cash() -> float:
    raw = get_setting("cash")
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if value >= 0 and isfinite(value) else 0.0


def set_cash(amount: float) -> None:
    if amount < 0 or not isfinite(amount):
        raise ValueError("cash amount must be non-negative")
    set_setting("cash", str(amount))


def list_items() -> list[WatchItem]:
    with connect() as c:
        rows = c.execute("SELECT symbol, value FROM watchlist ORDER BY added_at").fetchall()
    return [WatchItem(symbol=r["symbol"], value=r["value"]) for r in rows]


def has(symbol: str) -> bool:
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    return row is not None


def add(symbol: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol.upper(),)
        )


def remove(symbol: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
