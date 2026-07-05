"""Completeness-weighted confidence grade, independent of the LLM's own claim."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..schemas import Confidence, TickerData
from .schemas import IndicatorScorecard

if TYPE_CHECKING:
    from ..profiles.base import Profile

_ORDER = {"low": 0, "medium": 1, "high": 2}


class ConfidenceAssessment(BaseModel):
    computed: Confidence
    completeness: float
    reasons: list[str]


def clamp_confidence(*grades: Confidence) -> Confidence:
    """The lowest of the given grades — confidence can only be pulled down."""
    return min(grades, key=_ORDER.__getitem__)


def _cap(grade: Confidence, cap: Confidence) -> Confidence:
    return clamp_confidence(grade, cap)


def compute_confidence(
    data: TickerData, scorecard: IndicatorScorecard, profile: Profile
) -> ConfidenceAssessment:
    """Weight data completeness into a low/medium/high grade, then hard-cap it
    to medium on any source error and to low if there's no quote at all."""
    reasons: list[str] = []
    completeness = 0.0
    weights = profile.confidence_weights

    if data.quote is not None:
        completeness += weights.quote
        if weights.quote > 0:
            reasons.append("quote present")

    fund_fields = (
        data.fundamentals.market_cap,
        data.fundamentals.pe_ratio,
        data.fundamentals.forward_pe,
        data.fundamentals.profit_margin,
        data.fundamentals.revenue,
    )
    fund_count = sum(v is not None for v in fund_fields)
    if fund_count >= 2:
        completeness += weights.fundamentals
    if weights.fundamentals > 0:
        reasons.append(f"fundamentals {fund_count}/5 fields")

    if data.financials is not None:
        completeness += weights.financials
    if weights.financials > 0:
        if data.financials is not None:
            reasons.append("financials present")
        else:
            reasons.append("financials missing")

    if weights.news > 0:
        if len(data.news) >= 3:
            completeness += weights.news
        reasons.append(f"news {len(data.news)} items")
    elif len(data.news) >= 3:
        completeness += weights.news

    if weights.macro > 0:
        if data.macro is not None:
            completeness += weights.macro
            reasons.append("macro present")
        else:
            reasons.append("macro missing")
    elif data.macro is not None:
        completeness += weights.macro

    present_indicators = sum(i.value is not None for i in scorecard.indicators)
    total_indicators = len(scorecard.indicators)
    completeness += scorecard.data_completeness * weights.scorecard
    if weights.scorecard > 0:
        reasons.append(f"scorecard {present_indicators}/{total_indicators} indicators")

    if completeness >= 0.75:
        computed: Confidence = "high"
    elif completeness >= 0.45:
        computed = "medium"
    else:
        computed = "low"

    if any(status == "error" for status in data.sources.values()):
        computed = _cap(computed, "medium")
        for source, status in sorted(data.sources.items()):
            if status == "error":
                reasons.append(f"{source}: error")
    if data.quote is None:
        computed = _cap(computed, "low")
        reasons.append("quote missing: cap low")
    if weights.dark_company_cap and data.financials is None:
        computed = _cap(computed, "low")
        reasons.append("no SEC financials: dark-company cap low")

    return ConfidenceAssessment(
        computed=computed,
        completeness=round(completeness, 4),
        reasons=reasons,
    )
