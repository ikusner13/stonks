"""Ticker data orchestration: merge Yahoo + Finnhub behind an intraday cache."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Awaitable, TypeVar

from ..cache import with_cache
from ..config import FINNHUB_API_KEY
from ..schemas import Fundamentals, NewsItem, Quote, SourceStatus, TickerData
from . import finnhub, yahoo
from .sec import fetch_financials
from .macro import fetch_macro

logger = logging.getLogger(__name__)

# Intraday TTL: repeated research/discovery within the window reuse one fetch.
DATA_TTL_MS = 15 * 60_000

# Cap merged headlines so the LLM ground-truth prompt stays lean (and cheap).
MAX_NEWS = 15

T = TypeVar("T")


def _is_empty(result: object) -> bool:
    if result is None or result == []:
        return True
    if isinstance(result, Fundamentals):
        return all(
            getattr(result, field) is None
            for field in ("market_cap", "pe_ratio", "forward_pe", "profit_margin", "revenue")
        )
    return False


async def _capture(
    name: str,
    coro: Awaitable[T],
    fallback: T,
    statuses: dict[str, SourceStatus],
) -> T:
    try:
        result = await coro
        statuses[name] = "empty" if _is_empty(result) else "ok"
        return result
    except Exception:
        logger.warning("data fetch failed: %s", name, exc_info=True)
        statuses[name] = "error"
        return fallback


async def _none() -> None:
    return None


async def _empty() -> list[NewsItem]:
    return []


async def fetch_ticker_data(symbol: str, *, fresh: bool = False) -> TickerData:
    """Merged Yahoo + Finnhub + SEC + FRED ticker data, cached 15 min per symbol.

    Per-source failures degrade to a safe fallback with ``sources[name] =
    "error"`` rather than failing the whole call."""
    async def produce() -> dict:
        data = await _fetch_uncached(symbol, fresh=fresh)
        return data.model_dump()

    value, _ = await with_cache("data", symbol.upper(), DATA_TTL_MS, produce, fresh=fresh)
    return TickerData.model_validate(value)


async def _fetch_uncached(symbol: str, *, fresh: bool = False) -> TickerData:
    use_finnhub = bool(FINNHUB_API_KEY)
    statuses: dict[str, SourceStatus] = {}
    quote_statuses: dict[str, SourceStatus] = {}
    news_statuses: dict[str, SourceStatus] = {}

    if os.getenv("FRED_API_KEY"):
        macro_coro = _capture("macro", fetch_macro(fresh=fresh), None, statuses)
    else:
        statuses["macro"] = "disabled"
        macro_coro = _none()

    (
        yahoo_quote, fundamentals, yahoo_news, finnhub_quote, finnhub_news,
        financials, macro,
    ) = await asyncio.gather(
        _capture("yahoo_quote", asyncio.to_thread(yahoo.fetch_quote, symbol), None, quote_statuses),
        _capture(
            "fundamentals",
            asyncio.to_thread(yahoo.fetch_fundamentals, symbol),
            Fundamentals(),
            statuses,
        ),
        _capture("yahoo_news", asyncio.to_thread(yahoo.fetch_news, symbol), [], news_statuses),
        _capture("finnhub_quote", finnhub.fetch_quote(symbol), None, quote_statuses)
        if use_finnhub
        else _none(),
        _capture("finnhub_news", finnhub.fetch_news(symbol), [], news_statuses)
        if use_finnhub
        else _empty(),
        _capture("financials", fetch_financials(symbol, fresh=fresh), None, statuses),
        macro_coro,
    )

    # Prefer Finnhub's live quote when available, else fall back to Yahoo.
    quote: Quote | None = finnhub_quote or yahoo_quote
    if quote is not None:
        statuses["quote"] = "ok"
    elif "error" in quote_statuses.values():
        statuses["quote"] = "error"
    else:
        statuses["quote"] = "empty"

    # Merge news, dedupe by URL (Finnhub first).
    seen: set[str] = set()
    news: list[NewsItem] = []
    for n in [*finnhub_news, *yahoo_news]:
        if n.url in seen:
            continue
        seen.add(n.url)
        news.append(n)
        if len(news) >= MAX_NEWS:
            break
    if news:
        statuses["news"] = "ok"
    elif "error" in news_statuses.values():
        statuses["news"] = "error"
    else:
        statuses["news"] = "empty"

    return TickerData(
        symbol=symbol,
        fetched_at=datetime.now(UTC).isoformat(),
        quote=quote,
        fundamentals=fundamentals,
        news=news,
        financials=financials,
        macro=macro,
        sources=statuses,
    )
