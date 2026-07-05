"""Server-side watchlist store (SQLite). Single-user personal tool — no auth.

Replaces the original browser-localStorage watchlist so the list can be
server-rendered and survives across devices. One table, two columns: a symbol
and an optional dollar position used by the portfolio page.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
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
