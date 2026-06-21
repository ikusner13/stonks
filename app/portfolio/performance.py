from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import quantstats_lumi as qs
import yfinance as yf
from pydantic import BaseModel


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


def _fetch_returns(symbols: list[str], lookback_days: int) -> pd.DataFrame | None:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    if raw is None or raw.empty:
        return None
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    close = close.dropna(axis=1, how="all").dropna(axis=0, how="any")
    if close.shape[0] < 30:
        return None
    return close.pct_change().dropna()


def _build_portfolio_returns(weights: dict[str, float], lookback_days: int) -> pd.Series | None:
    symbols = list(weights.keys())
    returns = _fetch_returns(symbols, lookback_days)
    if returns is None or returns.empty:
        return None

    available = [s for s in symbols if s in returns.columns]
    if not available:
        return None

    total_w = sum(weights[s] for s in available)
    if total_w <= 0:
        return None
    normalized = {s: weights[s] / total_w for s in available}

    portfolio = sum(returns[s] * normalized[s] for s in available)
    return portfolio


def _compute_sync(
    weights: dict[str, float], lookback_days: int, benchmark: str
) -> PerformanceMetrics | None:
    portfolio = _build_portfolio_returns(weights, lookback_days)
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
        bench_returns = _fetch_returns([benchmark], lookback_days)
        if bench_returns is not None and benchmark in bench_returns.columns:
            bench_series = bench_returns[benchmark]
            benchmark_cagr = float(qs.stats.cagr(bench_series))
    except Exception:
        pass

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
    )


async def compute_performance(
    weights: dict[str, float],
    lookback_days: int = 730,
    benchmark: str = "SPY",
) -> PerformanceMetrics | None:
    return await asyncio.to_thread(_compute_sync, weights, lookback_days, benchmark)


def _tearsheet_sync(weights: dict[str, float], lookback_days: int, benchmark: str) -> str | None:
    portfolio = _build_portfolio_returns(weights, lookback_days)
    if portfolio is None or len(portfolio) < 30:
        return None

    bench_returns: pd.Series | None = None
    try:
        bench_df = _fetch_returns([benchmark], lookback_days)
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
