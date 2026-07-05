"""Pydantic models for a single indicator and the full scorecard."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Signal = Literal["bullish", "bearish", "neutral", "unavailable"]
Unit = Literal["pct", "ratio", "days", "usd", "count"]


class Indicator(BaseModel):
    key: str
    label: str
    value: float | None
    unit: Unit
    signal: Signal
    detail: str


class IndicatorScorecard(BaseModel):
    symbol: str
    asof: str
    profile: str = "largecap"
    indicators: list[Indicator]
    bullish: int
    bearish: int
    neutral: int
    unavailable: int
    data_completeness: float

    def numeric_values(self) -> list[float]:
        """All indicator values — added to the fabrication-check allowed set."""
        return [i.value for i in self.indicators if i.value is not None]
