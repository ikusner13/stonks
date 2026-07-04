"""Macroeconomic context via FRED (fredapi)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from pydantic import BaseModel

from ..cache import with_cache

_MACRO_TTL_MS = 6 * 60 * 60_000  # 6 hours


class MacroContext(BaseModel):
    fed_funds_rate: float | None = None
    cpi_yoy: float | None = None
    treasury_10y: float | None = None
    unemployment_rate: float | None = None
    gdp_growth: float | None = None
    asof: str | None = None

    def numeric_values(self) -> list[float]:
        return [
            v
            for v in (
                self.fed_funds_rate,
                self.cpi_yoy,
                self.treasury_10y,
                self.unemployment_rate,
                self.gdp_growth,
            )
            if v is not None
        ]


def _fetch_fred_sync(api_key: str) -> dict:
    from fredapi import Fred

    fred = Fred(api_key=api_key)

    def latest(series_id: str) -> float | None:
        try:
            s = fred.get_series(series_id).dropna()
            return float(s.iloc[-1]) if len(s) else None
        except Exception:
            return None

    def cpi_yoy() -> float | None:
        try:
            s = fred.get_series("CPIAUCSL").dropna()
            if len(s) < 13:
                return None
            return float((s.iloc[-1] / s.iloc[-13] - 1) * 100)
        except Exception:
            return None

    # DGS10 is daily and can have trailing NaNs — dropna handles it.
    return {
        "fed_funds_rate": latest("FEDFUNDS"),
        "cpi_yoy": cpi_yoy(),
        "treasury_10y": latest("DGS10"),
        "unemployment_rate": latest("UNRATE"),
        "gdp_growth": latest("A191RL1Q225SBEA"),
        "asof": datetime.now(UTC).isoformat(),
    }


async def fetch_macro(*, fresh: bool = False) -> MacroContext | None:
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return None

    async def produce() -> dict:
        return await asyncio.to_thread(_fetch_fred_sync, api_key)

    value, _ = await with_cache("macro", "latest", _MACRO_TTL_MS, produce, fresh=fresh)
    if not value:
        return None
    return MacroContext.model_validate(value)
