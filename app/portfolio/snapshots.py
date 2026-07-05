"""Daily NAV snapshots for the real portfolio equity curve."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from ..db import connect
from .holdings import PortfolioValuation


class NavSnapshot(BaseModel):
    day: str
    total_value: float
    cash: float
    total_with_cash: float
    total_cost: float
    unrealized_pl: float


class NavSeries(BaseModel):
    points: list[NavSnapshot]
    change_1d: float | None
    change_1d_pct: float | None
    change_total: float | None
    change_total_pct: float | None


def init_snapshots_db() -> None:
    """Create the NAV snapshots table if it does not exist."""
    with connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS nav_snapshots (
                day TEXT PRIMARY KEY,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                total_with_cash REAL NOT NULL,
                total_cost REAL NOT NULL,
                unrealized_pl REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def record_snapshot(valuation: PortfolioValuation) -> bool:
    """Record today's UTC NAV when the valuation is complete and positive."""
    if valuation.unpriced_symbols or valuation.total_with_cash <= 0:
        return False

    day = datetime.now(UTC).date().isoformat()
    with connect() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO nav_snapshots (
                day, total_value, cash, total_with_cash, total_cost, unrealized_pl
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                valuation.total_value,
                valuation.cash,
                valuation.total_with_cash,
                valuation.total_cost,
                valuation.total_unrealized_pl,
            ),
        )
    return True


def list_snapshots(limit: int = 365) -> list[NavSnapshot]:
    """Return recent NAV snapshots in ascending day order."""
    if limit <= 0:
        return []
    with connect() as c:
        rows = c.execute(
            """
            SELECT day, total_value, cash, total_with_cash, total_cost, unrealized_pl
            FROM nav_snapshots
            ORDER BY day DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        NavSnapshot(
            day=r["day"],
            total_value=r["total_value"],
            cash=r["cash"],
            total_with_cash=r["total_with_cash"],
            total_cost=r["total_cost"],
            unrealized_pl=r["unrealized_pl"],
        )
        for r in reversed(rows)
    ]


def build_nav_series(points: list[NavSnapshot]) -> NavSeries:
    change_1d: float | None = None
    change_1d_pct: float | None = None
    change_total: float | None = None
    change_total_pct: float | None = None

    if len(points) >= 2:
        latest = points[-1].total_with_cash
        previous = points[-2].total_with_cash
        first = points[0].total_with_cash
        change_1d = latest - previous
        change_1d_pct = change_1d / previous if previous != 0 else None
        change_total = latest - first
        change_total_pct = change_total / first if first != 0 else None

    return NavSeries(
        points=points,
        change_1d=change_1d,
        change_1d_pct=change_1d_pct,
        change_total=change_total,
        change_total_pct=change_total_pct,
    )
