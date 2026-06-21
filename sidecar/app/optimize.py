"""Mean-variance portfolio optimization over historical daily returns.

Self-contained: fetches its own price history via yfinance (same vendor as the
core app's yahoo-finance2), runs skfolio's MeanRisk, and reports annualized
return / volatility / Sharpe computed consistently with numpy so optimal,
current, and efficient-frontier points all use the same annualization.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from skfolio import RiskMeasure
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.preprocessing import prices_to_returns

from .schemas import (
    FrontierPoint,
    Holding,
    OptimizeRequest,
    OptimizeResponse,
    PortfolioMetrics,
)

TRADING_DAYS = 252


class NoDataError(ValueError):
    """Raised when no requested symbol returns usable price history."""


def _fetch_prices(symbols: list[str], lookback_days: int) -> pd.DataFrame:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    raw = yf.download(
        symbols,
        start=start,
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if raw is None or raw.empty:
        raise NoDataError("yfinance returned no data")

    # Multi-ticker -> MultiIndex (PriceType, Ticker); single ticker -> flat cols.
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])

    # Drop symbols with no data, then any rows still carrying gaps so the
    # covariance matrix is computed on a complete, aligned panel.
    close = close.dropna(axis=1, how="all").dropna(axis=0, how="any")
    if close.shape[0] < 2 or close.shape[1] == 0:
        raise NoDataError("insufficient overlapping price history")
    return close


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
        size = h.value if h.value is not None else h.shares
        if size:
            sizes[h.symbol] = sizes.get(h.symbol, 0.0) + float(size)
    total = sum(sizes.values())
    if total <= 0:
        return None
    return {sym: sizes.get(sym, 0.0) / total for sym in available}


def optimize(req: OptimizeRequest) -> OptimizeResponse:
    requested = list(dict.fromkeys(h.symbol for h in req.holdings))  # dedupe, keep order
    prices = _fetch_prices(requested, req.lookback_days)
    available = list(prices.columns)
    warnings = [f"{s}: no price data, dropped" for s in requested if s not in available]

    returns = prices_to_returns(prices)
    n = len(available)

    # Optimal weights.
    if n == 1:
        opt_vec = np.array([1.0])
    else:
        obj = (
            ObjectiveFunction.MAXIMIZE_RATIO
            if req.objective == "max_sharpe"
            else ObjectiveFunction.MINIMIZE_RISK
        )
        model = MeanRisk(risk_measure=RiskMeasure.VARIANCE, objective_function=obj)
        model.fit(returns)
        opt_vec = np.asarray(model.weights_, dtype=float).ravel()

    optimal = PortfolioMetrics(
        weights={sym: round(float(w), 6) for sym, w in zip(available, opt_vec)},
        **_metrics(opt_vec, returns, req.risk_free_rate),
    )

    # Current allocation (only if the caller supplied position sizes).
    current = None
    cur_weights = _current_weights(req.holdings, available)
    if cur_weights is not None:
        cur_vec = np.array([cur_weights[s] for s in available])
        current = PortfolioMetrics(
            weights={s: round(w, 6) for s, w in cur_weights.items()},
            **_metrics(cur_vec, returns, req.risk_free_rate),
        )

    # Efficient frontier (min-risk sweep).
    frontier: list[FrontierPoint] = []
    if n >= 2 and req.frontier_points >= 2:
        ef = MeanRisk(
            risk_measure=RiskMeasure.VARIANCE,
            objective_function=ObjectiveFunction.MINIMIZE_RISK,
            efficient_frontier_size=req.frontier_points,
        )
        ef.fit(returns)
        for row in np.atleast_2d(np.asarray(ef.weights_, dtype=float)):
            frontier.append(FrontierPoint(**_metrics(row, returns, req.risk_free_rate)))

    return OptimizeResponse(
        asof=datetime.now(UTC).isoformat(),
        objective=req.objective,
        lookback_days=req.lookback_days,
        symbols=available,
        optimal=optimal,
        current=current,
        efficient_frontier=frontier,
        warnings=warnings,
    )
