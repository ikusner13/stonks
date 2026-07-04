"""Full research pipeline with a persistent result cache keyed by symbol+day+mode.

A cache hit makes ZERO LLM and (via the data cache) zero network calls.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..cache import with_cache
from ..data import fetch_ticker_data
from ..ledger import record_call
from ..schemas import ResearchResult
from .critic import ReviewMode, research_ticker_reviewed
from .usage import annotate_run

logger = logging.getLogger(__name__)

# Reports are stable for a trading day; re-opening one costs $0. The symbol+day
# key already scopes it, so a long TTL is fine.
REPORT_TTL_MS = 24 * 60 * 60_000


def _trading_day() -> str:
    return datetime.now(UTC).date().isoformat()  # UTC date; fine for a personal tool


async def research_ticker_cached(
    symbol: str, mode: ReviewMode = "thorough", *, fresh: bool = False
) -> ResearchResult:
    sym = symbol.upper()
    key = f"{sym}:{_trading_day()}:{mode}"

    async def produce() -> dict:
        ticker = await fetch_ticker_data(sym, fresh=fresh)
        report, critique, revised = await research_ticker_reviewed(sym, ticker, mode)
        return ResearchResult(
            ticker=ticker, report=report, critique=critique, revised=revised
        ).model_dump()

    value, hit = await with_cache("report", key, REPORT_TTL_MS, produce, fresh=fresh)
    result = ResearchResult.model_validate(value)
    if hit:
        annotate_run(cached=True, revised=result.revised)
    else:
        try:
            record_call(result, mode)
        except Exception:
            logger.exception("failed to record research call")
    return result
