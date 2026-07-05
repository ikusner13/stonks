"""Yahoo Finance access via yfinance (replaces the TS yahoo-finance2).

These are synchronous (yfinance is blocking); the orchestrator offloads them to
a thread pool so they don't stall the event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

import yfinance as yf

from ..schemas import Fundamentals, NewsItem, Quote


def fetch_quote(symbol: str) -> Quote | None:
    info = yf.Ticker(symbol).info
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price is None:
        return None
    return Quote(
        price=float(price),
        currency=info.get("currency") or "USD",
        change=float(info.get("regularMarketChange") or 0.0),
        change_percent=float(info.get("regularMarketChangePercent") or 0.0),
    )


def fetch_fundamentals(symbol: str) -> Fundamentals:
    info = yf.Ticker(symbol).info
    f = Fundamentals()
    if (v := info.get("marketCap")) is not None:
        f.market_cap = float(v)
    if (v := info.get("trailingPE")) is not None:
        f.pe_ratio = float(v)
    if (v := info.get("forwardPE")) is not None:
        f.forward_pe = float(v)
    if (v := info.get("profitMargins")) is not None:
        f.profit_margin = float(v)
    if (v := info.get("totalRevenue")) is not None:
        f.revenue = float(v)
    if (v := info.get("exchange")) is not None:
        f.exchange = str(v)
    if (v := info.get("floatShares")) is not None:
        f.float_shares = float(v)
    if (v := info.get("sharesOutstanding")) is not None:
        f.shares_outstanding = float(v)
    if (v := info.get("sector")) is not None and isinstance(v, str):
        f.sector = v
    if (v := info.get("industry")) is not None and isinstance(v, str):
        f.industry = v
    return f


def _news_title(item: dict) -> str | None:
    # yfinance flattened the news shape into a nested "content" object in recent
    # versions; support both the old flat keys and the new nested ones.
    content = item.get("content") or item
    return content.get("title")


def _news_url(item: dict) -> str | None:
    content = item.get("content") or item
    url = content.get("canonicalUrl") or content.get("clickThroughUrl")
    if isinstance(url, dict):
        return url.get("url")
    return url or content.get("link")


def _news_published(item: dict) -> str:
    content = item.get("content") or item
    pub = content.get("pubDate") or content.get("providerPublishTime")
    if isinstance(pub, (int, float)):
        return datetime.fromtimestamp(pub, tz=UTC).isoformat()
    return pub or datetime.now(UTC).isoformat()


def _news_source(item: dict) -> str:
    content = item.get("content") or item
    provider = content.get("provider")
    if isinstance(provider, dict):
        return provider.get("displayName") or "Yahoo"
    return content.get("publisher") or provider or "Yahoo"


def fetch_news(symbol: str, count: int = 10) -> list[NewsItem]:
    raw = yf.Ticker(symbol).news or []
    out: list[NewsItem] = []
    for item in raw[:count]:
        title = _news_title(item)
        url = _news_url(item)
        if not title or not url:
            continue
        out.append(
            NewsItem(
                title=title,
                url=url,
                published_at=_news_published(item),
                source=_news_source(item),
            )
        )
    return out
