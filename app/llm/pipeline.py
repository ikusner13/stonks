"""Full research pipeline with a persistent result cache keyed by symbol+day+mode.

A cache hit makes ZERO LLM and (via the data cache) zero network calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..cache import with_cache
from ..data import fetch_ticker_data
from ..schemas import ResearchResult
from .critic import ReviewMode, research_ticker_reviewed
from .usage import annotate_run

# Reports are stable for a trading day; re-opening one costs $0. The symbol+day
# key already scopes it, so a long TTL is fine.
REPORT_TTL_MS = 24 * 60 * 60_000


class InsufficientDataError(RuntimeError):
    def __init__(self, symbol: str, sources: dict[str, str]):
        self.symbol = symbol
        self.sources = sources
        super().__init__(f"no usable market data for {symbol}")


def _trading_day() -> str:
    return datetime.now(UTC).date().isoformat()  # UTC date; fine for a personal tool


async def research_ticker_cached(
    symbol: str, mode: ReviewMode = "thorough", *, fresh: bool = False
) -> ResearchResult:
    sym = symbol.upper()
    key = f"{sym}:{_trading_day()}:{mode}"

    async def produce() -> dict:
        ticker = await fetch_ticker_data(sym, fresh=fresh)
        if ticker.quote is None and ticker.fundamentals.market_cap is None:
            raise InsufficientDataError(sym, ticker.sources)
        report, critique, revised = await research_ticker_reviewed(sym, ticker, mode)
        return ResearchResult(
            ticker=ticker, report=report, critique=critique, revised=revised
        ).model_dump()

    value, hit = await with_cache("report", key, REPORT_TTL_MS, produce, fresh=fresh)
    result = ResearchResult.model_validate(value)
    if hit:
        annotate_run(cached=True, revised=result.revised)
    return result
