"""Pydantic models — the LLM structured-output contracts and data shapes.

Field descriptions are load-bearing: Pydantic AI feeds them to the model as the
JSON schema, exactly as the original Zod ``.describe()`` calls did.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["low", "medium", "high"]
Severity = Literal["low", "medium", "high"]
SourceStatus = Literal["ok", "empty", "error", "disabled"]

# --- Market data ------------------------------------------------------------


class Quote(BaseModel):
    price: float
    currency: str
    change: float
    change_percent: float


class Fundamentals(BaseModel):
    # Known fields are explicit; anything extra a source provides is kept too,
    # so the LLM ground truth stays faithful to what we actually fetched.
    model_config = ConfigDict(extra="allow")

    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    profit_margin: float | None = None
    revenue: float | None = None
    exchange: str | None = None
    float_shares: float | None = None
    shares_outstanding: float | None = None
    sector: str | None = None
    industry: str | None = None


class NewsItem(BaseModel):
    title: str
    url: str
    published_at: str
    source: str


class TickerData(BaseModel):
    symbol: str
    fetched_at: str
    quote: Quote | None = None
    fundamentals: Fundamentals = Field(default_factory=Fundamentals)
    news: list[NewsItem] = Field(default_factory=list)
    financials: SecFinancials | None = None
    macro: MacroContext | None = None
    sources: dict[str, SourceStatus] = Field(default_factory=dict)


# --- Research report --------------------------------------------------------


class KeyMetric(BaseModel):
    label: str = Field(description="Name of the metric, e.g. 'P/E ratio'.")
    value: str = Field(
        description=(
            "The figure RESTATED verbatim from the provided data, as a string. "
            "Never invent or estimate a number."
        )
    )
    interpretation: str = Field(
        description="Why this metric matters and how to read it in context."
    )


class Thesis(BaseModel):
    bull: list[str] = Field(
        description="Concise arguments supporting an investment (the bull case)."
    )
    bear: list[str] = Field(
        description="Concise arguments against an investment (the bear case)."
    )


class TickerReport(BaseModel):
    symbol: str = Field(description="The ticker symbol, e.g. AAPL.")
    company_name: str = Field(description="The full company name.")
    summary: str = Field(
        description="A neutral 2-4 sentence overview of the company and its current situation."
    )
    thesis: Thesis = Field(
        description="Opposing investment cases derived only from the provided data."
    )
    key_metrics: list[KeyMetric] = Field(
        description="Metrics restated from the provided data with plain-language interpretation."
    )
    valuation_context: str = Field(
        description=(
            "How the stock appears valued given the provided figures, "
            "without inventing numbers."
        )
    )
    indicator_view: str = Field(
        default="",
        description=(
            "How the quantitative indicator scorecard reads overall: which signals "
            "agree, which conflict, and what evidence would resolve each conflict. "
            "Reference indicators by their label."
        ),
    )
    risks: list[str] = Field(description="Key risks to the investment thesis.")
    things_to_investigate: list[str] = Field(
        description="Open questions or data points a reader should research further."
    )
    confidence: Confidence = Field(
        description="Confidence in this report given the completeness of the provided data."
    )


# --- Critic -----------------------------------------------------------------


class FabricationCheck(BaseModel):
    passed: bool = Field(
        description="True if every number in the report is traceable to the ground-truth data."
    )
    details: str = Field(
        description="Explanation of the fabrication assessment, naming any untraceable figures."
    )


class Issue(BaseModel):
    severity: Severity = Field(
        description=(
            "How serious the issue is: high means a fabrication or "
            "materially misleading claim."
        )
    )
    field: str = Field(
        description="The report field the issue concerns, e.g. 'thesis.bull' or 'key_metrics[2].value'."
    )
    problem: str = Field(description="What is wrong with this part of the report.")
    fix: str = Field(description="Concrete instruction for how to correct the problem.")


class Critique(BaseModel):
    fabrication_check: FabricationCheck = Field(
        description="Verdict on whether the report invents numbers not present in the ground truth."
    )
    issues: list[Issue] = Field(
        description="Specific, actionable problems found in the report. Empty if none."
    )
    suggested_confidence: Confidence = Field(
        description="The confidence the report SHOULD state given the completeness of the data."
    )
    overall_assessment: str = Field(
        description="A brief skeptical summary of the report's quality and trustworthiness."
    )


# --- Discovery --------------------------------------------------------------

Source = Literal["screener", "theme"]


class Candidate(BaseModel):
    symbol: str
    name: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    rationale: str
    source: Source


class DiscoveryResult(BaseModel):
    goal: str
    interpretation: str
    candidates: list[Candidate]


# --- Full research result (cached unit) -------------------------------------


class ResearchResult(BaseModel):
    ticker: TickerData
    report: TickerReport
    critique: Critique
    revised: bool
    scorecard: IndicatorScorecard | None = None
    confidence_assessment: ConfidenceAssessment | None = None
    profile: str = "largecap"
    profile_reason: str = ""


# Deferred imports to avoid circular dependency (data/ imports schemas).
# model_rebuild() resolves the forward references in TickerData.
from .data.sec import SecFinancials  # noqa: E402
from .data.macro import MacroContext  # noqa: E402
from .indicators.schemas import IndicatorScorecard  # noqa: E402
from .indicators.confidence import ConfidenceAssessment  # noqa: E402

TickerData.model_rebuild()
ResearchResult.model_rebuild()
