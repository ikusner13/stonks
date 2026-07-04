import numpy as np
import pandas as pd

from app.data.sec import SecFinancials
from app.indicators.engine import compute_indicators
from app.schemas import Fundamentals, TickerData


def _series(values: list[float] | np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values)))


def _by_key(data: TickerData, close: pd.Series | None, spy: pd.Series | None = None):
    indicators = compute_indicators(close, spy, data, None)
    return {i.key: i for i in indicators}


def _ticker(
    *,
    pe_ratio: float | None = None,
    market_cap: float | None = None,
    profit_margin: float | None = None,
    financials: SecFinancials | None = None,
) -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        fundamentals=Fundamentals(
            pe_ratio=pe_ratio,
            market_cap=market_cap,
            profit_margin=profit_margin,
        ),
        financials=financials,
    )


def test_steady_ramp_has_bullish_momentum_and_trend():
    close = _series(np.geomspace(100, 130, 260))
    spy = _series(np.geomspace(100, 112, 260))
    out = _by_key(_ticker(), close, spy)

    assert out["momentum_12_1"].signal == "bullish"
    assert out["trend_200d"].signal == "bullish"
    assert out["max_drawdown_1y"].signal == "neutral"


def test_crash_from_peak_flags_distance_and_drawdown():
    close = _series([100.0] * 160 + [200.0] * 40 + [100.0] * 60)
    out = _by_key(_ticker(), close)

    assert out["pct_from_52w_high"].signal == "bearish"
    assert out["max_drawdown_1y"].signal == "bearish"


def test_short_series_makes_six_month_momentum_unavailable():
    close = _series(np.geomspace(100, 115, 100))
    out = _by_key(_ticker(), close)

    assert out["momentum_12_1"].signal == "unavailable"
    assert out["momentum_6m"].signal == "unavailable"
    assert out["momentum_6m"].value is None


def test_short_window_details_state_actual_session_count():
    close = _series([100.0] * 90 + [80.0] * 10)
    out = _by_key(_ticker(), close)

    assert "last 100 sessions" in out["pct_from_52w_high"].detail
    assert "last 100 sessions" in out["max_drawdown_1y"].detail


def test_fundamental_threshold_boundaries():
    data = _ticker(
        pe_ratio=50,
        market_cap=1_000,
        profit_margin=0.0,
        financials=SecFinancials(
            free_cash_flow=10,
            total_debt=500,
            total_assets=1_000,
        ),
    )
    out = _by_key(data, None)

    assert out["earnings_yield"].value == 0.02
    assert out["earnings_yield"].signal == "neutral"
    assert out["fcf_yield"].value == 0.01
    assert out["fcf_yield"].signal == "neutral"
    assert out["debt_to_assets"].value == 0.5
    assert out["debt_to_assets"].signal == "neutral"

    bullish = _by_key(
        _ticker(
            pe_ratio=10,
            market_cap=1_000,
            profit_margin=0.151,
            financials=SecFinancials(free_cash_flow=60, total_debt=100, total_assets=1_000),
        ),
        None,
    )
    assert bullish["earnings_yield"].signal == "bullish"
    assert bullish["fcf_yield"].signal == "bullish"
    assert bullish["profit_margin"].signal == "bullish"
    assert bullish["debt_to_assets"].signal == "bullish"


def test_negative_net_income_without_pe_is_bearish_unvalued():
    out = _by_key(_ticker(financials=SecFinancials(net_income=-1)), None)

    assert out["earnings_yield"].value is None
    assert out["earnings_yield"].signal == "bearish"


def test_missing_price_history_keeps_fundamental_indicators():
    data = _ticker(pe_ratio=10, profit_margin=0.2)
    out = _by_key(data, None)

    assert out["momentum_12_1"].signal == "unavailable"
    assert out["trend_200d"].signal == "unavailable"
    assert out["earnings_yield"].signal == "bullish"
    assert out["profit_margin"].signal == "bullish"
