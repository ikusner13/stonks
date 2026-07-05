from __future__ import annotations

from .base import ConfidenceWeights, LiquiditySizing, Profile, Threshold

PENNY_RESEARCH_STANCE = (
    "This is a PENNY / MICRO-CAP stock (profile: penny). Operate with elevated "
    "skepticism: assume dilution and promotional activity until the filings show "
    "otherwise. Weigh survival metrics (cash runway, share dilution, filing recency, "
    "liquidity) above valuation metrics. News items may be paid promotion — treat "
    "headlines as claims, not facts. The bull case must clear a higher evidentiary bar "
    "than the bear case. Missing or stale SEC filings are themselves bearish evidence, "
    "not merely missing data."
)

PENNY = Profile(
    key="penny",
    label="Penny / micro-cap",
    indicator_keys=(
        "trend_50d",
        "trend_200d",
        "pct_from_52w_high",
        "momentum_6m",
        "realized_vol_90d",
        "max_drawdown_1y",
        "avg_dollar_volume_20d",
        "relative_volume",
        "zero_volume_days_90d",
        "share_dilution",
        "cash_runway_months",
        "filing_recency_days",
        "debt_to_assets",
        "float_shares",
        "days_to_earnings",
    ),
    thresholds={
        "trend_50d": Threshold(0.0, -0.05),
        "trend_200d": Threshold(0.0, -0.02),
        "pct_from_52w_high": Threshold(-0.10, -0.50),
        "realized_vol_90d": Threshold(float("nan"), 1.50),
        "max_drawdown_1y": Threshold(float("nan"), -0.60),
        "avg_dollar_volume_20d": Threshold(2_000_000, 200_000),
        "zero_volume_days_90d": Threshold(1, 5, reversed_polarity=True),
        "share_dilution": Threshold(0.03, 0.15, reversed_polarity=True),
        "cash_runway_months": Threshold(24, 6),
        "filing_recency_days": Threshold(float("nan"), 150, reversed_polarity=True),
        "debt_to_assets": Threshold(0.15, 0.50, reversed_polarity=True),
    },
    confidence_weights=ConfidenceWeights(
        quote=0.25,
        fundamentals=0.10,
        financials=0.30,
        news=0.0,
        macro=0.0,
        scorecard=0.35,
        dark_company_cap=True,
    ),
    sizing_bands={
        "high": (0.01, 0.03),
        "medium": (0.005, 0.015),
        "low": (0.0025, 0.0075),
    },
    liquidity_sizing=LiquiditySizing(max_participation=0.10, max_days_to_exit=3),
    optimizer_included=False,
    research_stance=PENNY_RESEARCH_STANCE,
)
