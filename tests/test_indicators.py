from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.data.sec import SecFinancials
from app.indicators.engine import IndicatorContext, _signal, compute_indicators, compute_scorecard
from app.profiles.largecap import LARGECAP
from app.profiles.penny import PENNY
from app.schemas import Fundamentals, TickerData


def _series(values: list[float] | np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values)))


def _by_key(
    data: TickerData,
    close: pd.Series | None,
    spy: pd.Series | None = None,
    *,
    volume: pd.Series | None = None,
    profile=LARGECAP,
):
    indicators = compute_indicators(
        IndicatorContext(
            close=close,
            volume=volume,
            spy_close=spy,
            data=data,
            days_to_earnings=None,
            profile=profile,
        )
    )
    return {i.key: i for i in indicators}


def _ticker(
    *,
    pe_ratio: float | None = None,
    market_cap: float | None = None,
    profit_margin: float | None = None,
    float_shares: float | None = None,
    financials: SecFinancials | None = None,
) -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        fundamentals=Fundamentals(
            pe_ratio=pe_ratio,
            market_cap=market_cap,
            profit_margin=profit_margin,
            float_shares=float_shares,
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


def test_new_price_and_volume_builders_happy_paths():
    close = _series([10.0] * 49 + [11.0] + [12.0] * 40 + [13.0] * 20 + [14.0] * 70)
    volume = _series([100.0] * 90 + [200.0] * 19 + [500.0] + [300.0] * 70)
    volume.iloc[-90:-86] = 0
    out = _by_key(_ticker(), close, volume=volume, profile=PENNY)

    assert out["trend_50d"].value == round(close.iloc[-1] / close.iloc[-50:].mean() - 1, 4)
    assert out["avg_dollar_volume_20d"].value == round(
        float((close.iloc[-20:] * volume.iloc[-20:]).mean()), 4
    )
    assert out["relative_volume"].value == round(volume.iloc[-1] / volume.iloc[-21:-1].mean(), 4)
    assert out["zero_volume_days_90d"].value == 4


def test_new_price_and_volume_builders_unavailable_paths():
    out = _by_key(_ticker(), _series([10.0] * 49), volume=_series([100.0] * 20), profile=PENNY)

    assert out["trend_50d"].signal == "unavailable"
    assert out["zero_volume_days_90d"].signal == "unavailable"

    no_volume = _by_key(_ticker(), _series([10.0] * 20), volume=None, profile=PENNY)
    assert no_volume["avg_dollar_volume_20d"].signal == "unavailable"

    zero_denominator = _by_key(
        _ticker(), _series([10.0] * 21), volume=_series([0.0] * 20 + [100.0]), profile=PENNY
    )
    assert zero_denominator["relative_volume"].signal == "unavailable"
    assert zero_denominator["relative_volume"].value is None


def test_share_dilution_builder_happy_path_and_missing_prior():
    out = _by_key(
        _ticker(
            financials=SecFinancials(
                shares_outstanding=120,
                shares_outstanding_prior=100,
                prior_period="2024",
                fiscal_period="2025",
            )
        ),
        None,
        profile=PENNY,
    )

    assert out["share_dilution"].value == 0.2
    assert out["share_dilution"].signal == "bearish"
    assert "2024 to 2025" in out["share_dilution"].detail

    missing = _by_key(
        _ticker(financials=SecFinancials(shares_outstanding=120)),
        None,
        profile=PENNY,
    )
    assert missing["share_dilution"].signal == "unavailable"


def test_cash_runway_builder_math_forced_bullish_and_missing_cash():
    out = _by_key(
        _ticker(
            financials=SecFinancials(cash_and_equivalents=10_000_000, operating_cash_flow=-30_000_000)
        ),
        None,
        profile=PENNY,
    )

    assert out["cash_runway_months"].value == 4
    assert out["cash_runway_months"].signal == "bearish"

    self_funding = _by_key(
        _ticker(financials=SecFinancials(cash_and_equivalents=10, operating_cash_flow=1)),
        None,
        profile=PENNY,
    )
    assert self_funding["cash_runway_months"].value is None
    assert self_funding["cash_runway_months"].signal == "bullish"
    assert self_funding["cash_runway_months"].detail == "operating cash flow positive — self-funding"

    missing_cash = _by_key(
        _ticker(financials=SecFinancials(operating_cash_flow=-1)),
        None,
        profile=PENNY,
    )
    assert missing_cash["cash_runway_months"].signal == "unavailable"


def test_filing_recency_and_float_builders():
    filed = (datetime.now(UTC).date() - timedelta(days=45)).isoformat()
    out = _by_key(
        _ticker(float_shares=12_500_000, financials=SecFinancials(filed=filed)),
        None,
        profile=PENNY,
    )

    assert out["filing_recency_days"].value == 45
    assert out["filing_recency_days"].signal == "neutral"
    assert out["float_shares"].value == 12_500_000
    assert "12.5M" in out["float_shares"].detail

    missing = _by_key(_ticker(), None, profile=PENNY)
    assert missing["filing_recency_days"].signal == "unavailable"
    assert missing["float_shares"].signal == "unavailable"


def test_penny_threshold_spot_checks():
    assert _signal(PENNY, "realized_vol_90d", 1.4) == "neutral"
    assert _signal(PENNY, "realized_vol_90d", 1.6) == "bearish"
    assert _signal(PENNY, "share_dilution", 0.20) == "bearish"
    assert _signal(PENNY, "cash_runway_months", 4) == "bearish"


async def test_scorecard_cache_key_includes_profile(monkeypatch):
    keys: list[str] = []

    async def fake_with_cache(namespace, key, ttl_ms, produce, *, fresh=False):
        keys.append(key)
        return await produce(), False

    monkeypatch.setattr("app.indicators.engine.with_cache", fake_with_cache)
    monkeypatch.setattr("app.indicators.engine._fetch_history", lambda symbol: {"close": {}, "volume": {}})
    monkeypatch.setattr("app.indicators.engine._fetch_days_to_earnings", lambda symbol: None)

    data = _ticker()
    largecap = await compute_scorecard("sym", data, profile=LARGECAP, fresh=True)
    penny = await compute_scorecard("sym", data, profile=PENNY, fresh=True)

    assert largecap.profile == "largecap"
    assert penny.profile == "penny"
    assert keys[0].startswith("SYM:largecap:")
    assert keys[1].startswith("SYM:penny:")
    assert keys[0] != keys[1]
