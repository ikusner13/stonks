"""Ticker data orchestration: merge Yahoo + Finnhub behind an intraday cache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Awaitable, Callable, TypeVar

from ..cache import with_cache
from ..config import FINNHUB_API_KEY
from ..schemas import Fundamentals, NewsItem, Quote, TickerData
from . import finnhub, yahoo
from .sec import fetch_financials
from .macro import fetch_macro

# Intraday TTL: repeated research/discovery within the window reuse one fetch.
DATA_TTL_MS = 15 * 60_000

# Cap merged headlines so the LLM ground-truth prompt stays lean (and cheap).
MAX_NEWS = 15

T = TypeVar("T")


async def _safe(coro: Awaitable[T], fallback: T) -> T:
    try:
        return await coro
    except Exception:
        return fallback


async def _safe_thread(fn: Callable[[], T], fallback: T) -> T:
    return await _safe(asyncio.to_thread(fn), fallback)


async def _none() -> None:
    return None


async def _empty() -> list[NewsItem]:
    return []


async def fetch_ticker_data(symbol: str, *, fresh: bool = False) -> TickerData:
    async def produce() -> dict:
        data = await _fetch_uncached(symbol)
        return data.model_dump()

    value, _ = await with_cache("data", symbol.upper(), DATA_TTL_MS, produce, fresh=fresh)
    return TickerData.model_validate(value)


async def _fetch_uncached(symbol: str) -> TickerData:
    use_finnhub = bool(FINNHUB_API_KEY)

    (
        yahoo_quote, fundamentals, yahoo_news, finnhub_quote, finnhub_news,
        financials, macro,
    ) = await asyncio.gather(
        _safe_thread(lambda: yahoo.fetch_quote(symbol), None),
        _safe_thread(lambda: yahoo.fetch_fundamentals(symbol), Fundamentals()),
        _safe_thread(lambda: yahoo.fetch_news(symbol), []),
        _safe(finnhub.fetch_quote(symbol), None) if use_finnhub else _none(),
        _safe(finnhub.fetch_news(symbol), []) if use_finnhub else _empty(),
        _safe(fetch_financials(symbol), None),
        _safe(fetch_macro(), None),
    )

    # Prefer Finnhub's live quote when available, else fall back to Yahoo.
    quote: Quote | None = finnhub_quote or yahoo_quote

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

    return TickerData(
        symbol=symbol,
        fetched_at=datetime.now(UTC).isoformat(),
        quote=quote,
        fundamentals=fundamentals,
        news=news,
        financials=financials,
        macro=macro,
    )
