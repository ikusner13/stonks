from __future__ import annotations

from .base import ConfidenceWeights, Profile, Threshold

LARGECAP = Profile(
    key="largecap",
    label="Large cap",
    indicator_keys=(
        "momentum_12_1",
        "momentum_6m",
        "pct_from_52w_high",
        "trend_200d",
        "realized_vol_90d",
        "beta_1y",
        "max_drawdown_1y",
        "earnings_yield",
        "fcf_yield",
        "profit_margin",
        "debt_to_assets",
        "days_to_earnings",
    ),
    thresholds={
        "momentum_12_1": Threshold(0.10, -0.10),
        "momentum_6m": Threshold(0.08, -0.08),
        "pct_from_52w_high": Threshold(-0.05, -0.20),
        "trend_200d": Threshold(0.0, -0.02),
        "realized_vol_90d": Threshold(float("nan"), 0.60),
        "max_drawdown_1y": Threshold(float("nan"), -0.40),
        "earnings_yield": Threshold(0.06, 0.02),
        "fcf_yield": Threshold(0.05, 0.01),
        "profit_margin": Threshold(0.15, 0.0),
        "debt_to_assets": Threshold(0.15, 0.50, reversed_polarity=True),
    },
    confidence_weights=ConfidenceWeights(
        quote=0.25,
        fundamentals=0.15,
        financials=0.20,
        news=0.10,
        macro=0.05,
        scorecard=0.25,
        dark_company_cap=False,
    ),
    sizing_bands={
        "high": (0.05, 0.10),
        "medium": (0.03, 0.06),
        "low": (0.015, 0.03),
    },
    liquidity_sizing=None,
    optimizer_included=True,
    research_stance="",
)
