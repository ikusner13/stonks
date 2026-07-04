from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from pydantic import BaseModel, Field

from ..config import DB_PATH
from ..data import fetch_ticker_data

logger = logging.getLogger(__name__)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class Holding(BaseModel):
    symbol: str
    shares: float
    avg_cost: float | None = None


class HoldingValuation(BaseModel):
    symbol: str
    shares: float
    avg_cost: float | None
    price: float | None
    market_value: float | None
    cost_value: float | None
    unrealized_pl: float | None
    unrealized_pl_pct: float | None
    weight: float | None


class PortfolioValuation(BaseModel):
    holdings: list[HoldingValuation]
    total_value: float
    total_cost: float
    total_unrealized_pl: float
    total_unrealized_pl_pct: float
    asof: str
    unpriced_symbols: list[str] = Field(default_factory=list)


def init_holdings_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                symbol TEXT PRIMARY KEY,
                shares REAL NOT NULL,
                avg_cost REAL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def list_holdings() -> list[Holding]:
    with _conn() as c:
        rows = c.execute("SELECT symbol, shares, avg_cost FROM holdings ORDER BY symbol").fetchall()
    return [Holding(symbol=r["symbol"], shares=r["shares"], avg_cost=r["avg_cost"]) for r in rows]


def upsert_holding(symbol: str, shares: float, avg_cost: float | None) -> None:
    sym = symbol.upper()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO holdings (symbol, shares, avg_cost, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
                shares = excluded.shares,
                avg_cost = excluded.avg_cost,
                updated_at = excluded.updated_at
            """,
            (sym, shares, avg_cost),
        )


def remove_holding(symbol: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM holdings WHERE symbol = ?", (symbol.upper(),))


async def value_holdings() -> PortfolioValuation:
    holdings = list_holdings()
    asof = datetime.now(UTC).isoformat()

    if not holdings:
        return PortfolioValuation(
            holdings=[],
            total_value=0.0,
            total_cost=0.0,
            total_unrealized_pl=0.0,
            total_unrealized_pl_pct=0.0,
            asof=asof,
            unpriced_symbols=[],
        )

    async def fetch_price(symbol: str) -> float | None:
        try:
            data = await fetch_ticker_data(symbol)
            return data.quote.price if data.quote else None
        except Exception:
            logger.warning("price fetch failed for %s", symbol, exc_info=True)
            return None

    prices = await asyncio.gather(*[fetch_price(h.symbol) for h in holdings])
    price_map = {h.symbol: p for h, p in zip(holdings, prices)}

    valuations: list[HoldingValuation] = []
    total_value = 0.0
    total_cost = 0.0
    total_unrealized_pl = 0.0
    unpriced_symbols: list[str] = []

    for h in holdings:
        price = price_map.get(h.symbol)
        market_value = h.shares * price if price is not None else None
        cost_value = h.shares * h.avg_cost if h.avg_cost is not None else None
        unrealized_pl = (
            (market_value - cost_value)
            if (market_value is not None and cost_value is not None)
            else None
        )
        unrealized_pl_pct = (
            (unrealized_pl / cost_value)
            if (unrealized_pl is not None and cost_value and cost_value != 0)
            else None
        )

        if market_value is not None:
            total_value += market_value
        else:
            unpriced_symbols.append(h.symbol)
        if unrealized_pl is not None and cost_value is not None:
            total_cost += cost_value
            total_unrealized_pl += unrealized_pl

        valuations.append(HoldingValuation(
            symbol=h.symbol,
            shares=h.shares,
            avg_cost=h.avg_cost,
            price=price,
            market_value=market_value,
            cost_value=cost_value,
            unrealized_pl=unrealized_pl,
            unrealized_pl_pct=unrealized_pl_pct,
            weight=None,
        ))

    # Fill in weights now that we know total_value
    for v in valuations:
        if v.market_value is not None and total_value > 0:
            v.weight = v.market_value / total_value

    total_unrealized_pl_pct = (total_unrealized_pl / total_cost) if total_cost != 0 else 0.0

    return PortfolioValuation(
        holdings=valuations,
        total_value=total_value,
        total_cost=total_cost,
        total_unrealized_pl=total_unrealized_pl,
        total_unrealized_pl_pct=total_unrealized_pl_pct,
        asof=asof,
        unpriced_symbols=unpriced_symbols,
    )
