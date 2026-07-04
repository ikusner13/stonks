"""Mean-variance portfolio optimization over historical daily returns.

Folded in from the former HTTP sidecar — now an in-process call. Fetches price
history via yfinance, runs skfolio's MeanRisk, and reports annualized
return / volatility / Sharpe computed consistently with numpy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator
from skfolio import RiskMeasure
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.preprocessing import prices_to_returns

from .history import NoDataError, fetch_price_history

__all__ = ["Holding", "NoDataError", "OptimizeRequest", "OptimizeResult", "optimize"]

TRADING_DAYS = 252

Objective = Literal["max_sharpe", "min_risk"]

DISCLAIMER = (
    "Research context only, not investment advice. Mean-variance weights are "
    "derived from historical returns and assume the past is representative."
)


class Holding(BaseModel):
    symbol: str
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
    max_weight: float = Field(default=0.35, gt=0, le=1.0)


class PortfolioMetrics(BaseModel):
    weights: dict[str, float]
    expected_return: float  # annualized
    volatility: float  # annualized
    sharpe: float


class FrontierPoint(BaseModel):
    expected_return: float
    volatility: float
    sharpe: float


class OptimizeResult(BaseModel):
    asof: str
    objective: Objective
    lookback_days: int
    symbols: list[str]
    optimal: PortfolioMetrics
    current: PortfolioMetrics | None = None
    efficient_frontier: list[FrontierPoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


def _fetch_prices(symbols: list[str], lookback_days: int) -> tuple[pd.DataFrame, list[str]]:
    return fetch_price_history(symbols, lookback_days)


def _metrics(weights: np.ndarray, returns: pd.DataFrame, rf: float) -> dict:
    mean = returns.mean().to_numpy() * TRADING_DAYS
    cov = returns.cov().to_numpy() * TRADING_DAYS
    exp_ret = float(weights @ mean)
    var = float(weights @ cov @ weights)
    vol = float(np.sqrt(var)) if var > 0 else 0.0
    sharpe = (exp_ret - rf) / vol if vol > 0 else 0.0
    return {"expected_return": exp_ret, "volatility": vol, "sharpe": sharpe}


def _current_weights(holdings: list[Holding], available: list[str]) -> dict[str, float] | None:
    sizes: dict[str, float] = {}
    for h in holdings:
        if h.symbol not in available:
            continue
        if h.value:
            sizes[h.symbol] = sizes.get(h.symbol, 0.0) + float(h.value)
    total = sum(sizes.values())
    if total <= 0:
        return None
    return {sym: sizes.get(sym, 0.0) / total for sym in available}


def optimize(req: OptimizeRequest) -> OptimizeResult:
    requested = list(dict.fromkeys(h.symbol for h in req.holdings))  # dedupe, keep order
    prices, excluded = _fetch_prices(requested, req.lookback_days)
    available = list(prices.columns)
    warnings = [
        f"{s}: no price data, dropped"
        for s in requested
        if s not in available and s not in excluded
    ]
    warnings.extend(f"{s}: insufficient price history, excluded" for s in excluded)

    returns = prices_to_returns(prices)
    n = len(available)

    if n == 1:
        opt_vec = np.array([1.0])
    else:
        obj = (
            ObjectiveFunction.MAXIMIZE_RATIO
            if req.objective == "max_sharpe"
            else ObjectiveFunction.MINIMIZE_RISK
        )
        cap = max(req.max_weight, 1.0 / n)
        if cap != req.max_weight:
            warnings.append(f"per-asset cap relaxed to {cap:.0%} for {n} assets")
        model = MeanRisk(
            risk_measure=RiskMeasure.VARIANCE,
            objective_function=obj,
            max_weights=cap,
            min_weights=0.0,
            risk_free_rate=req.risk_free_rate,
        )
        model.fit(returns)
        opt_vec = np.asarray(model.weights_, dtype=float).ravel()

    optimal = PortfolioMetrics(
        weights={sym: round(float(w), 6) for sym, w in zip(available, opt_vec)},
        **_metrics(opt_vec, returns, req.risk_free_rate),
    )

    current = None
    cur_weights = _current_weights(req.holdings, available)
    if cur_weights is not None:
        cur_vec = np.array([cur_weights[s] for s in available])
        current = PortfolioMetrics(
            weights={s: round(w, 6) for s, w in cur_weights.items()},
            **_metrics(cur_vec, returns, req.risk_free_rate),
        )

    frontier: list[FrontierPoint] = []
    if n >= 2 and req.frontier_points >= 2:
        ef = MeanRisk(
            risk_measure=RiskMeasure.VARIANCE,
            objective_function=ObjectiveFunction.MINIMIZE_RISK,
            efficient_frontier_size=req.frontier_points,
            max_weights=cap,
            min_weights=0.0,
            risk_free_rate=req.risk_free_rate,
        )
        ef.fit(returns)
        for row in np.atleast_2d(np.asarray(ef.weights_, dtype=float)):
            frontier.append(FrontierPoint(**_metrics(row, returns, req.risk_free_rate)))

    return OptimizeResult(
        asof=datetime.now(UTC).isoformat(),
        objective=req.objective,
        lookback_days=req.lookback_days,
        symbols=available,
        optimal=optimal,
        current=current,
        efficient_frontier=frontier,
        warnings=warnings,
    )
