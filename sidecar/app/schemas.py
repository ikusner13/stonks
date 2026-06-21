from typing import Literal

from pydantic import BaseModel, Field, field_validator

Objective = Literal["max_sharpe", "min_risk"]


class Holding(BaseModel):
    symbol: str
    # Optional position size, used only to compute the *current* allocation for
    # comparison. Either dollar value or share count; if both given, value wins.
    value: float | None = Field(default=None, ge=0)
    shares: float | None = Field(default=None, ge=0)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol must be non-empty")
        return v


class OptimizeRequest(BaseModel):
    holdings: list[Holding] = Field(min_length=1)
    objective: Objective = "max_sharpe"
    lookback_days: int = Field(default=730, ge=60, le=3650)
    risk_free_rate: float = Field(default=0.0, ge=0, le=0.2)
    frontier_points: int = Field(default=20, ge=0, le=100)


class PortfolioMetrics(BaseModel):
    weights: dict[str, float]
    expected_return: float  # annualized
    volatility: float  # annualized
    sharpe: float


class FrontierPoint(BaseModel):
    expected_return: float
    volatility: float
    sharpe: float


class OptimizeResponse(BaseModel):
    asof: str
    objective: Objective
    lookback_days: int
    symbols: list[str]
    optimal: PortfolioMetrics
    current: PortfolioMetrics | None = None
    efficient_frontier: list[FrontierPoint] = []
    warnings: list[str] = []
    disclaimer: str = (
        "Research context only, not investment advice. Mean-variance weights are "
        "derived from historical returns and assume the past is representative."
    )
