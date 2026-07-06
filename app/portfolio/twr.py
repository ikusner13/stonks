"""Time-weighted return from NAV snapshots and external cash flows.

TWR chain-links per-period returns with flows stripped out, so it measures the
allocation's performance independent of deposit/withdrawal timing - the number
to compare against a benchmark (docs/methodology.md §7).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from math import isfinite, prod

from pydantic import BaseModel

from ..cache import with_cache
from .history import fetch_price_history
from .snapshots import NavSnapshot, list_snapshots
from .transactions import list_transactions

MIN_SNAPSHOTS = 2
MIN_SPAN_DAYS = 14
TWR_TTL_MS = 24 * 60 * 60_000
BENCHMARK = "SPY"
NOTE = (
    "TWR strips out deposit and withdrawal timing so it is comparable to a benchmark; "
    "MWR, shown elsewhere, is the number for how your actual dollars did."
)


class TWRSummary(BaseModel):
    twr_cumulative: float
    twr_annualized: float | None
    window_start: str
    window_end: str
    num_periods: int
    benchmark: str
    benchmark_cumulative: float | None
    excess_cumulative: float | None
    note: str


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def compute_twr(
    snapshots: list[NavSnapshot],
    flows: list[tuple[str, float]],
) -> tuple[float, int, str, str] | None:
    """Pure chain-linking; returns cumulative TWR, periods, start, and end."""
    ordered = sorted(snapshots, key=lambda s: s.day)
    if len(ordered) < MIN_SNAPSHOTS:
        return None

    start = _parse_day(ordered[0].day)
    end = _parse_day(ordered[-1].day)
    if start is None or end is None:
        return None
    if (end - start).days < MIN_SPAN_DAYS:
        return None

    dated_flows: list[tuple[date, float]] = []
    for raw_day, amount in flows:
        flow_day = _parse_day(raw_day)
        if flow_day is None:
            return None
        flow_amount = float(amount)
        if not isfinite(flow_amount):
            return None
        dated_flows.append((flow_day, flow_amount))

    period_returns: list[float] = []
    for prev, cur in zip(ordered, ordered[1:]):
        prev_day = _parse_day(prev.day)
        cur_day = _parse_day(cur.day)
        if prev_day is None or cur_day is None:
            return None

        prev_nav = float(prev.total_with_cash)
        cur_nav = float(cur.total_with_cash)
        if prev_nav <= 0 or not isfinite(prev_nav) or not isfinite(cur_nav):
            return None

        net_flow = sum(
            amount for flow_day, amount in dated_flows if prev_day < flow_day <= cur_day
        )
        one_plus_return = (cur_nav - net_flow) / prev_nav
        if one_plus_return <= 0 or not isfinite(one_plus_return):
            return None
        period_returns.append(one_plus_return)

    return prod(period_returns) - 1, len(period_returns), ordered[0].day, ordered[-1].day


def _external_flows() -> list[tuple[str, float]]:
    flows: list[tuple[str, float]] = []
    for txn in list_transactions(limit=10_000):
        if txn.side == "deposit":
            flows.append((txn.ts, txn.amount))
        elif txn.side == "withdraw":
            flows.append((txn.ts, -txn.amount))
    return flows


async def _benchmark_return(window_start: str, window_end: str) -> float | None:
    start = _parse_day(window_start)
    end = _parse_day(window_end)
    if start is None or end is None:
        return None
    lookback_days = max((datetime.now(UTC).date() - start).days + 7, MIN_SPAN_DAYS)
    try:
        prices, _ = await asyncio.to_thread(fetch_price_history, [BENCHMARK], lookback_days)
    except Exception:
        return None

    window = prices.loc[(prices.index.date >= start) & (prices.index.date <= end)]
    if window.shape[0] < 2 or BENCHMARK not in window.columns:
        return None
    first = float(window[BENCHMARK].iloc[0])
    last = float(window[BENCHMARK].iloc[-1])
    if first <= 0 or not isfinite(first) or not isfinite(last):
        return None
    return last / first - 1


async def compute_twr_summary(*, fresh: bool = False) -> TWRSummary | None:
    snapshots = list_snapshots(limit=365)
    computed = compute_twr(snapshots, _external_flows())
    if computed is None:
        return None

    twr_cumulative, num_periods, window_start, window_end = computed
    day = datetime.now(UTC).date().isoformat()
    cache_key = f"{window_end}:{num_periods}:{day}"

    async def produce() -> dict | None:
        start = _parse_day(window_start)
        end = _parse_day(window_end)
        if start is None or end is None:
            return None
        span_days = (end - start).days
        annualized = (
            (1 + twr_cumulative) ** (365.25 / span_days) - 1
            if span_days >= 365
            else None
        )
        benchmark_cumulative = await _benchmark_return(window_start, window_end)
        excess = (
            twr_cumulative - benchmark_cumulative
            if benchmark_cumulative is not None
            else None
        )
        return TWRSummary(
            twr_cumulative=twr_cumulative,
            twr_annualized=annualized,
            window_start=window_start,
            window_end=window_end,
            num_periods=num_periods,
            benchmark=BENCHMARK,
            benchmark_cumulative=benchmark_cumulative,
            excess_cumulative=excess,
            note=NOTE,
        ).model_dump()

    value, _hit = await with_cache("twr", cache_key, TWR_TTL_MS, produce, fresh=fresh)
    return TWRSummary.model_validate(value) if value is not None else None
