from __future__ import annotations

from ..schemas import TickerData
from .base import Profile, ProfileKey
from .largecap import LARGECAP
from .penny import PENNY

PROFILES: dict[ProfileKey, Profile] = {"largecap": LARGECAP, "penny": PENNY}

PENNY_PRICE_MAX = 5.0
PENNY_MARKET_CAP_MAX = 75_000_000
OTC_EXCHANGES = {"PNK", "OTC", "OEM", "OQB", "OQX"}


def select_profile(data: TickerData, override: ProfileKey | None = None) -> tuple[Profile, str]:
    """Deterministic profile selection."""
    if override is not None:
        return PROFILES[override], "manual override"

    exchange = data.fundamentals.exchange
    if exchange in OTC_EXCHANGES:
        return PENNY, f"OTC-listed ({exchange})"

    if data.quote is not None and data.quote.price < PENNY_PRICE_MAX:
        return PENNY, f"price ${data.quote.price:g} < $5"

    market_cap = data.fundamentals.market_cap
    if market_cap is not None and market_cap < PENNY_MARKET_CAP_MAX:
        return PENNY, f"market cap ${market_cap:.0f} < $75M"

    return LARGECAP, "default"


__all__ = [
    "LARGECAP",
    "OTC_EXCHANGES",
    "PENNY",
    "PENNY_MARKET_CAP_MAX",
    "PENNY_PRICE_MAX",
    "PROFILES",
    "Profile",
    "ProfileKey",
    "select_profile",
]
