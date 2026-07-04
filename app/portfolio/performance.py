from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime

import pandas as pd
import quantstats_lumi as qs
from pydantic import BaseModel, Field

from .history import NoDataError, fetch_price_history


class PerformanceMetrics(BaseModel):
    cagr: float
    total_return: float
    sharpe: float
    sortino: float
    volatility: float
    max_drawdown: float
    benchmark: str
    benchmark_cagr: float | None
    lookback_days: int
    asof: str
    excluded_symbols: list[str] = Field(default_factory=list)
    window_start: str | None = None
    window_end: str | None = None
    sample_days: int = 0


def _fetch_returns(
    symbols: list[str], lookback_days: int
) -> tuple[pd.DataFrame | None, list[str]]:
    try:
        close, excluded = fetch_price_history(symbols, lookback_days)
    except NoDataError:
        return None, []
    returns = close.pct_change().dropna()
    if returns.shape[0] < 30:
        return None, excluded
    return returns, excluded


def _build_portfolio_returns(
    weights: dict[str, float], lookback_days: int
) -> tuple[pd.Series | None, list[str]]:
    symbols = list(weights.keys())
    returns, excluded = _fetch_returns(symbols, lookback_days)
    if returns is None or returns.empty:
        return None, excluded

    available = [s for s in symbols if s in returns.columns]
    if not available:
        return None, excluded

    total_w = sum(weights[s] for s in available)
    if total_w <= 0:
        return None, excluded
    normalized = {s: weights[s] / total_w for s in available}

    portfolio = sum(returns[s] * normalized[s] for s in available)
    return portfolio, excluded


def _compute_sync(
    weights: dict[str, float], lookback_days: int, benchmark: str
) -> PerformanceMetrics | None:
    portfolio, excluded = _build_portfolio_returns(weights, lookback_days)
    if portfolio is None or len(portfolio) < 30:
        return None

    try:
        cagr_val = float(qs.stats.cagr(portfolio))
        total_return = float(qs.stats.comp(portfolio))
        sharpe_val = float(qs.stats.sharpe(portfolio))
        sortino_val = float(qs.stats.sortino(portfolio))
        vol_val = float(qs.stats.volatility(portfolio))
        mdd_val = float(qs.stats.max_drawdown(portfolio))
    except Exception:
        return None

    benchmark_cagr: float | None = None
    try:
        bench_returns, _ = _fetch_returns([benchmark], lookback_days)
        if bench_returns is not None and benchmark in bench_returns.columns:
            bench_series = bench_returns[benchmark]
            common = portfolio.index.intersection(bench_series.index)
            if len(common) >= 30:
                benchmark_cagr = float(qs.stats.cagr(bench_series.loc[common]))
    except Exception:
        pass

    window_start = str(portfolio.index[0].date()) if len(portfolio.index) else None
    window_end = str(portfolio.index[-1].date()) if len(portfolio.index) else None

    return PerformanceMetrics(
        cagr=cagr_val,
        total_return=total_return,
        sharpe=sharpe_val,
        sortino=sortino_val,
        volatility=vol_val,
        max_drawdown=mdd_val,
        benchmark=benchmark,
        benchmark_cagr=benchmark_cagr,
        lookback_days=lookback_days,
        asof=datetime.now(UTC).isoformat(),
        excluded_symbols=excluded,
        window_start=window_start,
        window_end=window_end,
        sample_days=len(portfolio),
    )


async def compute_performance(
    weights: dict[str, float],
    lookback_days: int = 730,
    benchmark: str = "SPY",
) -> PerformanceMetrics | None:
    return await asyncio.to_thread(_compute_sync, weights, lookback_days, benchmark)


def _tearsheet_sync(weights: dict[str, float], lookback_days: int, benchmark: str) -> str | None:
    portfolio, _ = _build_portfolio_returns(weights, lookback_days)
    if portfolio is None or len(portfolio) < 30:
        return None

    bench_returns: pd.Series | None = None
    try:
        bench_df, _ = _fetch_returns([benchmark], lookback_days)
        if bench_df is not None and benchmark in bench_df.columns:
            bench_returns = bench_df[benchmark]
    except Exception:
        pass

    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        qs.reports.html(portfolio, benchmark=bench_returns, output=path, title="Portfolio Tearsheet")
        return open(path).read()
    except Exception:
        return None


def tearsheet_html(
    weights: dict[str, float],
    lookback_days: int = 730,
    benchmark: str = "SPY",
) -> str | None:
    return _tearsheet_sync(weights, lookback_days, benchmark)
