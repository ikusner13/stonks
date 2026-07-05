"""Full research pipeline with a persistent result cache keyed by symbol+day+mode.

A cache hit makes ZERO LLM and (via the data cache) zero network calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..cache import with_cache
from ..data import fetch_ticker_data
from ..indicators.confidence import clamp_confidence, compute_confidence
from ..indicators.engine import compute_scorecard
from ..profiles import select_profile
from ..profiles.base import ProfileKey
from ..schemas import ResearchResult
from .critic import ReviewMode, research_ticker_reviewed
from .usage import annotate_run

# Reports are stable for a trading day; re-opening one costs $0. The symbol+day
# key already scopes it, so a long TTL is fine.
REPORT_TTL_MS = 24 * 60 * 60_000


class InsufficientDataError(RuntimeError):
    """Raised when a symbol has neither a quote nor a market cap to research."""

    def __init__(self, symbol: str, sources: dict[str, str]):
        self.symbol = symbol
        self.sources = sources
        super().__init__(f"no usable market data for {symbol}")


def _trading_day() -> str:
    return datetime.now(UTC).date().isoformat()  # UTC date; fine for a personal tool


async def research_ticker_cached(
    symbol: str,
    mode: ReviewMode = "thorough",
    *,
    profile_override: ProfileKey | None = None,
    fresh: bool = False,
) -> ResearchResult:
    """Full research pipeline (data → scorecard → confidence → critic chain),
    cached per symbol/day/mode for 24h. Raises ``InsufficientDataError``
    (never cached) if there's no usable market data for ``symbol``."""
    sym = symbol.upper()
    # The key carries the OVERRIDE (not the derived profile): deriving the profile
    # needs ticker data, and fetching before the cache read would break the
    # zero-network-on-cache-hit property above. Auto-selection is deterministic
    # for a symbol/day, so "auto" scopes the derived case fully.
    key = f"{sym}:{_trading_day()}:{mode}:{profile_override or 'auto'}"

    async def produce() -> dict:
        ticker = await fetch_ticker_data(sym, fresh=fresh)
        if ticker.quote is None and ticker.fundamentals.market_cap is None:
            raise InsufficientDataError(sym, ticker.sources)
        profile, profile_reason = select_profile(ticker, profile_override)
        scorecard = await compute_scorecard(sym, ticker, profile=profile, fresh=fresh)
        assessment = compute_confidence(ticker, scorecard, profile)
        report, critique, revised = await research_ticker_reviewed(
            sym, ticker, scorecard, profile, mode
        )
        report.confidence = clamp_confidence(
            report.confidence, critique.suggested_confidence, assessment.computed
        )
        return ResearchResult(
            ticker=ticker,
            report=report,
            critique=critique,
            revised=revised,
            scorecard=scorecard,
            confidence_assessment=assessment,
            profile=profile.key,
            profile_reason=profile_reason,
        ).model_dump()

    value, hit = await with_cache("report", key, REPORT_TTL_MS, produce, fresh=fresh)
    result = ResearchResult.model_validate(value)
    if hit:
        annotate_run(cached=True, revised=result.revised)
    return result
