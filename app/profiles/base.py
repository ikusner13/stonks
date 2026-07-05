from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from ..schemas import Confidence

ProfileKey = Literal["largecap", "penny"]


@dataclass(frozen=True)
class Threshold:
    bullish_at: float
    bearish_at: float
    reversed_polarity: bool = False


@dataclass(frozen=True)
class ConfidenceWeights:
    quote: float
    fundamentals: float
    financials: float
    news: float
    macro: float
    scorecard: float
    dark_company_cap: bool = False


@dataclass(frozen=True)
class LiquiditySizing:
    max_participation: float
    max_days_to_exit: int


@dataclass(frozen=True)
class Profile:
    key: ProfileKey
    label: str
    indicator_keys: tuple[str, ...]
    thresholds: Mapping[str, Threshold]
    confidence_weights: ConfidenceWeights
    sizing_bands: Mapping[Confidence, tuple[float, float]]
    liquidity_sizing: LiquiditySizing | None
    optimizer_included: bool
    research_stance: str
