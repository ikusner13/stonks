from app.portfolio.performance import BACKTEST_CAVEAT, PerformanceMetrics


def test_performance_metrics_include_calmar():
    metrics = PerformanceMetrics(
        cagr=0.1,
        total_return=0.2,
        sharpe=1.1,
        sortino=1.4,
        calmar=0.8,
        volatility=0.18,
        max_drawdown=-0.12,
        benchmark="SPY",
        benchmark_cagr=0.09,
        lookback_days=730,
        asof="2026-07-05T00:00:00Z",
    )

    assert metrics.calmar == 0.8
    assert "hindsight" in BACKTEST_CAVEAT
