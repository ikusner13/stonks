from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Literal, cast

import pandas as pd
import yfinance as yf
from pydantic import BaseModel

from . import db
from .schemas import Confidence, ResearchResult, Stance

Horizon = Literal["1w", "1m", "3m"]
HORIZON_DAYS: dict[Horizon, int] = {"1w": 7, "1m": 30, "3m": 91}
BENCHMARK = "SPY"

logger = logging.getLogger(__name__)


class Outcome(BaseModel):
    horizon: Horizon
    fwd_return: float
    bench_return: float

    @property
    def excess(self) -> float:
        return self.fwd_return - self.bench_return


class Call(BaseModel):
    id: int
    symbol: str
    as_of: str
    mode: str
    stance: Stance | None
    confidence: Confidence
    price: float | None
    revised: bool
    outcomes: dict[Horizon, Outcome]


class LedgerSummary(BaseModel):
    total_calls: int
    scored_calls: int
    hit_rate: dict[Horizon, float]
    avg_excess: dict[Horizon, float]
    n_directional: dict[Horizon, int]
    hit_rate_by_confidence: dict[Confidence, float]


def hit_for_stance(stance: Stance | None, excess: float) -> bool | None:
    if stance == "bullish":
        return excess > 0
    if stance == "bearish":
        return excess < 0
    return None


def _utc_day(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date().isoformat()


def record_call(result: ResearchResult, mode: str) -> None:
    """INSERT OR IGNORE a row derived from a freshly generated report."""
    db.init_db()
    quote = result.ticker.quote
    with db._conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO calls
              (symbol, as_of, mode, stance, confidence, price, revised)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.ticker.symbol.upper(),
                _utc_day(result.ticker.fetched_at),
                mode,
                result.report.stance,
                result.report.confidence,
                quote.price if quote else None,
                1 if result.revised else 0,
            ),
        )


def score_window(closes: pd.Series, as_of: date, days: int) -> float | None:
    """Return close-to-close return over the first bars on/after each date."""
    if not isinstance(closes, pd.Series) or closes.empty:
        return None

    series = closes.dropna().sort_index()
    if series.empty:
        return None

    idx = pd.to_datetime(series.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    normalized = idx.normalize()
    series.index = normalized

    start_target = pd.Timestamp(as_of)
    end_target = pd.Timestamp(as_of) + pd.Timedelta(days=days)

    start = series[series.index >= start_target]
    end = series[series.index >= end_target]
    if start.empty or end.empty:
        return None

    start_price = float(start.iloc[0])
    end_price = float(end.iloc[0])
    if start_price == 0:
        return None
    return (end_price / start_price) - 1


def _pending_pairs() -> list[dict]:
    db.init_db()
    values = ", ".join(f"('{h}', {d})" for h, d in HORIZON_DAYS.items())
    with db._conn() as c:
        rows = c.execute(
            f"""
            WITH horizons(horizon, days) AS (VALUES {values})
            SELECT c.id, c.symbol, c.as_of, h.horizon, h.days
            FROM calls c
            JOIN horizons h
            LEFT JOIN call_outcomes o
              ON o.call_id = c.id AND o.horizon = h.horizon
            WHERE date(c.as_of, '+' || h.days || ' days') < date('now')
              AND o.call_id IS NULL
            ORDER BY c.as_of, c.symbol, h.days
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _close_frame(raw: pd.DataFrame | pd.Series | None, symbols: list[str]) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    try:
        close = raw["Close"]
    except (KeyError, TypeError):
        return None
    if isinstance(close, pd.Series):
        return close.to_frame(symbols[0])
    return close


def _series_for(close: pd.DataFrame, symbol: str) -> pd.Series | None:
    if symbol in close.columns:
        return close[symbol]
    upper_map = {str(col).upper(): col for col in close.columns}
    column = upper_map.get(symbol.upper())
    if column is None:
        return None
    series = close[column]
    return series if isinstance(series, pd.Series) else None


def _download_closes(symbols: list[str], start: str) -> pd.DataFrame | None:
    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    return _close_frame(raw, symbols)


async def evaluate_pending() -> int:
    """Compute and insert matured outcomes; failures degrade to still-pending."""
    pending = _pending_pairs()
    if not pending:
        return 0

    symbols = sorted({p["symbol"] for p in pending} | {BENCHMARK})
    start = min(p["as_of"] for p in pending)
    try:
        close = await asyncio.to_thread(_download_closes, symbols, start)
    except Exception:
        logger.exception("call ledger yfinance download failed")
        return 0
    if close is None or close.empty:
        return 0

    rows: list[tuple[int, Horizon, float, float]] = []
    for pair in pending:
        symbol_series = _series_for(close, pair["symbol"])
        bench_series = _series_for(close, BENCHMARK)
        if symbol_series is None or bench_series is None:
            continue
        as_of = date.fromisoformat(pair["as_of"])
        days = int(pair["days"])
        fwd_return = score_window(symbol_series, as_of, days)
        bench_return = score_window(bench_series, as_of, days)
        if fwd_return is None or bench_return is None:
            continue
        rows.append(
            (
                int(pair["id"]),
                cast(Horizon, pair["horizon"]),
                fwd_return,
                bench_return,
            )
        )

    if not rows:
        return 0

    try:
        with db._conn() as c:
            before = c.total_changes
            c.executemany(
                """
                INSERT OR IGNORE INTO call_outcomes
                  (call_id, horizon, fwd_return, bench_return)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            return c.total_changes - before
    except Exception:
        logger.exception("call ledger outcome insert failed")
        return 0


def list_calls(limit: int = 200) -> list[Call]:
    db.init_db()
    with db._conn() as c:
        call_rows = c.execute(
            """
            SELECT id, symbol, as_of, mode, stance, confidence, price, revised
            FROM calls
            ORDER BY as_of DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        ids = [int(r["id"]) for r in call_rows]
        outcome_rows = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            outcome_rows = c.execute(
                f"""
                SELECT call_id, horizon, fwd_return, bench_return
                FROM call_outcomes
                WHERE call_id IN ({placeholders})
                ORDER BY call_id, horizon
                """,
                ids,
            ).fetchall()

    outcomes_by_call: dict[int, dict[Horizon, Outcome]] = {i: {} for i in ids}
    for row in outcome_rows:
        horizon = cast(Horizon, row["horizon"])
        outcomes_by_call[int(row["call_id"])][horizon] = Outcome(
            horizon=horizon,
            fwd_return=float(row["fwd_return"]),
            bench_return=float(row["bench_return"]),
        )

    return [
        Call(
            id=int(row["id"]),
            symbol=row["symbol"],
            as_of=row["as_of"],
            mode=row["mode"],
            stance=row["stance"],
            confidence=row["confidence"],
            price=row["price"],
            revised=bool(row["revised"]),
            outcomes=outcomes_by_call[int(row["id"])],
        )
        for row in call_rows
    ]


def summarize(calls: list[Call]) -> LedgerSummary:
    hit_rate: dict[Horizon, float] = {}
    avg_excess: dict[Horizon, float] = {}
    n_directional: dict[Horizon, int] = {}

    for horizon in HORIZON_DAYS:
        outcomes = [call.outcomes[horizon] for call in calls if horizon in call.outcomes]
        if outcomes:
            avg_excess[horizon] = sum(o.excess for o in outcomes) / len(outcomes)

        directional = [
            (call.stance, call.outcomes[horizon].excess)
            for call in calls
            if horizon in call.outcomes and call.stance in ("bullish", "bearish")
        ]
        if directional:
            n_directional[horizon] = len(directional)
            hits = sum(
                1 for stance, excess in directional if hit_for_stance(cast(Stance, stance), excess)
            )
            hit_rate[horizon] = hits / len(directional)

    by_conf: dict[Confidence, list[bool]] = {}
    for call in calls:
        outcome = call.outcomes.get("1m")
        if outcome is None or call.stance not in ("bullish", "bearish"):
            continue
        hit = hit_for_stance(call.stance, outcome.excess)
        if hit is not None:
            by_conf.setdefault(call.confidence, []).append(hit)

    return LedgerSummary(
        total_calls=len(calls),
        scored_calls=sum(1 for call in calls if call.outcomes),
        hit_rate=hit_rate,
        avg_excess=avg_excess,
        n_directional=n_directional,
        hit_rate_by_confidence={
            confidence: sum(hits) / len(hits) for confidence, hits in by_conf.items() if hits
        },
    )
