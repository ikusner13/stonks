"""Yahoo predefined equity screeners via yfinance (replaces yahoo-finance2 screener)."""

from __future__ import annotations

import yfinance as yf
from pydantic import BaseModel

# The predefined Yahoo screen keys, mirrored from the original ScreenId union.
SCREEN_IDS: list[str] = [
    "aggressive_small_caps",
    "conservative_foreign_funds",
    "day_gainers",
    "day_losers",
    "growth_technology_stocks",
    "high_yield_bond",
    "most_actives",
    "most_shorted_stocks",
    "portfolio_anchors",
    "small_cap_gainers",
    "solid_large_growth_funds",
    "solid_midcap_growth_funds",
    "top_mutual_funds",
    "undervalued_growth_stocks",
    "undervalued_large_caps",
]


class ScreenedQuote(BaseModel):
    symbol: str
    name: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    price: float | None = None
    change_percent: float | None = None


def run_screen(scr_id: str, count: int = 25) -> list[ScreenedQuote]:
    try:
        res = yf.screen(scr_id, count=count)
    except Exception:
        return []
    quotes = (res or {}).get("quotes", []) if isinstance(res, dict) else []
    out: list[ScreenedQuote] = []
    for q in quotes:
        sym = q.get("symbol")
        if not sym:
            continue
        out.append(
            ScreenedQuote(
                symbol=sym,
                name=q.get("shortName") or q.get("longName") or sym,
                market_cap=q.get("marketCap"),
                pe_ratio=q.get("trailingPE"),
                forward_pe=q.get("forwardPE"),
                price=q.get("regularMarketPrice"),
                change_percent=q.get("regularMarketChangePercent"),
            )
        )
    return out
