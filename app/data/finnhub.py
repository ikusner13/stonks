"""Finnhub REST access (optional; real-time US quotes + company news)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from ..config import FINNHUB_API_KEY
from ..schemas import NewsItem, Quote

BASE = "https://finnhub.io/api/v1"


async def fetch_quote(symbol: str) -> Quote | None:
    if not FINNHUB_API_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            f"{BASE}/quote", params={"symbol": symbol, "token": FINNHUB_API_KEY}
        )
    if res.status_code != 200:
        return None
    data = res.json()
    current = data.get("c")
    if not current:  # None or 0 -> no usable quote
        return None
    return Quote(
        price=float(current),
        currency="USD",
        change=float(data.get("d") or 0.0),
        change_percent=float(data.get("dp") or 0.0),
    )


async def fetch_news(symbol: str) -> list[NewsItem]:
    if not FINNHUB_API_KEY:
        return []
    to = datetime.now(UTC)
    frm = to - timedelta(days=14)
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            f"{BASE}/company-news",
            params={
                "symbol": symbol,
                "from": frm.date().isoformat(),
                "to": to.date().isoformat(),
                "token": FINNHUB_API_KEY,
            },
        )
    if res.status_code != 200:
        return []
    data = res.json()
    if not isinstance(data, list):
        return []
    out: list[NewsItem] = []
    for n in data:
        headline, url = n.get("headline"), n.get("url")
        if not headline or not url:
            continue
        ts = n.get("datetime")
        published = (
            datetime.fromtimestamp(ts, tz=UTC).isoformat()
            if ts
            else datetime.now(UTC).isoformat()
        )
        out.append(
            NewsItem(
                title=headline, url=url, published_at=published, source=n.get("source") or "Finnhub"
            )
        )
    return out
