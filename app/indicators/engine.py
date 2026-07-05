"""Deterministic, profile-driven indicator scorecards computed in code.

The LLM never computes indicator values; it only receives the scorecard.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import with_cache
from ..profiles.base import Profile
from ..profiles.largecap import LARGECAP
from ..schemas import TickerData
from .schemas import Indicator, IndicatorScorecard, Signal, Unit

HISTORY_LOOKBACK_DAYS = 420
HISTORY_TTL_MS = 24 * 60 * 60_000


@dataclass
class IndicatorContext:
    close: pd.Series | None
    volume: pd.Series | None
    spy_close: pd.Series | None
    data: TickerData
    days_to_earnings: int | None
    profile: Profile


Builder = Callable[[IndicatorContext], Indicator]


def _round(value: float) -> float:
    return round(float(value), 4)


def _unavailable(key: str, label: str, unit: Unit, detail: str) -> Indicator:
    return Indicator(key=key, label=label, value=None, unit=unit, signal="unavailable", detail=detail)


def _signal(profile: Profile, key: str, value: float) -> Signal:
    threshold = profile.thresholds.get(key)
    if threshold is None:
        return "neutral"
    if threshold.reversed_polarity:
        if value < threshold.bullish_at:
            return "bullish"
        if value > threshold.bearish_at:
            return "bearish"
        return "neutral"
    if value > threshold.bullish_at:
        return "bullish"
    if math.isnan(threshold.bullish_at):
        if threshold.bearish_at >= 0 and value > threshold.bearish_at:
            return "bearish"
        if threshold.bearish_at < 0 and value < threshold.bearish_at:
            return "bearish"
        return "neutral"
    if value < threshold.bearish_at:
        return "bearish"
    return "neutral"


def _price_indicator(
    key: str,
    label: str,
    unit: Unit,
    close: pd.Series | None,
    needed: int,
    compute: Callable[[pd.Series], Indicator],
) -> Indicator:
    if close is None or len(close.dropna()) < needed:
        return _unavailable(key, label, unit, f"needs at least {needed} price rows")
    return compute(close.dropna())


def _momentum_12_1(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        value = _round(series.iloc[-21] / series.iloc[-252] - 1)
        return Indicator(
            key="momentum_12_1",
            label="12-1 month momentum",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "momentum_12_1", value),
            detail=f"{value:+.1%} over months -12..-1",
        )

    return _price_indicator(
        "momentum_12_1", "12-1 month momentum", "pct", ctx.close, 252, compute
    )


def _momentum_6m(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        value = _round(series.iloc[-1] / series.iloc[-126] - 1)
        return Indicator(
            key="momentum_6m",
            label="6 month momentum",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "momentum_6m", value),
            detail=f"{value:+.1%} over 6 months",
        )

    return _price_indicator("momentum_6m", "6 month momentum", "pct", ctx.close, 126, compute)


def _pct_from_52w_high(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        window = series.iloc[-252:]
        value = _round(series.iloc[-1] / window.max() - 1)
        window_detail = "52w high" if len(window) >= 252 else f"high over last {len(window)} sessions"
        return Indicator(
            key="pct_from_52w_high",
            label="Distance from 52w high",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "pct_from_52w_high", value),
            detail=f"{value:.1%} from {window_detail}",
        )

    return _price_indicator(
        "pct_from_52w_high", "Distance from 52w high", "pct", ctx.close, 60, compute
    )


def _trend_200d(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        sma = float(series.iloc[-200:].mean())
        value = _round(series.iloc[-1] / sma - 1)
        return Indicator(
            key="trend_200d",
            label="Price vs 200d trend",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "trend_200d", value),
            detail=f"{value:+.1%} vs 200d moving average",
        )

    return _price_indicator("trend_200d", "Price vs 200d trend", "pct", ctx.close, 200, compute)


def _realized_vol_90d(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        returns = series.pct_change().dropna().iloc[-90:]
        if returns.empty:
            return _unavailable(
                "realized_vol_90d", "90d realized volatility", "pct", "needs return rows"
            )
        value = _round(float(returns.std()) * math.sqrt(252))
        return Indicator(
            key="realized_vol_90d",
            label="90d realized volatility",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "realized_vol_90d", value),
            detail=f"{value:.1%} annualized over 90d",
        )

    return _price_indicator(
        "realized_vol_90d", "90d realized volatility", "pct", ctx.close, 90, compute
    )


def _beta_1y(ctx: IndicatorContext) -> Indicator:
    if ctx.close is None or ctx.spy_close is None:
        return _unavailable("beta_1y", "1y beta vs SPY", "ratio", "needs symbol and SPY history")

    returns = ctx.close.dropna().pct_change().dropna()
    spy_returns = ctx.spy_close.dropna().pct_change().dropna()
    aligned = pd.concat([returns.rename("symbol"), spy_returns.rename("spy")], axis=1).dropna()
    aligned = aligned.iloc[-252:]
    spy_var = float(aligned["spy"].var()) if len(aligned) else 0.0
    if len(aligned) < 200 or spy_var == 0:
        return _unavailable("beta_1y", "1y beta vs SPY", "ratio", "needs 200 aligned return rows")

    value = _round(float(np.cov(aligned["symbol"], aligned["spy"])[0, 1]) / spy_var)
    return Indicator(
        key="beta_1y",
        label="1y beta vs SPY",
        value=value,
        unit="ratio",
        signal=_signal(ctx.profile, "beta_1y", value),
        detail=f"beta {value:.2f} vs SPY over 1y",
    )


def _max_drawdown_1y(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        window = series.iloc[-252:]
        value = _round(float((window / window.cummax() - 1).min()))
        window_detail = "1y" if len(window) >= 252 else f"last {len(window)} sessions"
        return Indicator(
            key="max_drawdown_1y",
            label="1y max drawdown",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "max_drawdown_1y", value),
            detail=f"{value:.1%} max drawdown over {window_detail}",
        )

    return _price_indicator("max_drawdown_1y", "1y max drawdown", "pct", ctx.close, 60, compute)


def _earnings_yield(ctx: IndicatorContext) -> Indicator:
    pe = ctx.data.fundamentals.pe_ratio
    if pe is not None and pe > 0:
        value = _round(1 / pe)
        return Indicator(
            key="earnings_yield",
            label="Earnings yield",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "earnings_yield", value),
            detail=f"{value:.1%} earnings yield from trailing P/E",
        )
    if (
        ctx.data.financials
        and ctx.data.financials.net_income is not None
        and ctx.data.financials.net_income < 0
    ):
        return Indicator(
            key="earnings_yield",
            label="Earnings yield",
            value=None,
            unit="pct",
            signal="bearish",
            detail="negative trailing earnings",
        )
    return _unavailable("earnings_yield", "Earnings yield", "pct", "trailing P/E unavailable")


def _fcf_yield(ctx: IndicatorContext) -> Indicator:
    fin = ctx.data.financials
    market_cap = ctx.data.fundamentals.market_cap
    if fin and fin.free_cash_flow is not None and market_cap and market_cap > 0:
        value = _round(fin.free_cash_flow / market_cap)
        return Indicator(
            key="fcf_yield",
            label="Free cash flow yield",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "fcf_yield", value),
            detail=f"{value:.1%} free cash flow yield",
        )
    return _unavailable("fcf_yield", "Free cash flow yield", "pct", "FCF or market cap unavailable")


def _profit_margin(ctx: IndicatorContext) -> Indicator:
    margin = ctx.data.fundamentals.profit_margin
    if margin is not None:
        value = _round(margin)
        return Indicator(
            key="profit_margin",
            label="Profit margin",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "profit_margin", value),
            detail=f"{value:.1%} profit margin",
        )
    return _unavailable("profit_margin", "Profit margin", "pct", "profit margin unavailable")


def _debt_to_assets(ctx: IndicatorContext) -> Indicator:
    fin = ctx.data.financials
    if fin and fin.total_debt is not None and fin.total_assets is not None and fin.total_assets > 0:
        value = _round(fin.total_debt / fin.total_assets)
        return Indicator(
            key="debt_to_assets",
            label="Debt to assets",
            value=value,
            unit="ratio",
            signal=_signal(ctx.profile, "debt_to_assets", value),
            detail=f"{value:.2f} debt/assets",
        )
    return _unavailable("debt_to_assets", "Debt to assets", "ratio", "debt or assets unavailable")


def _days_to_earnings(ctx: IndicatorContext) -> Indicator:
    if ctx.days_to_earnings is None:
        return _unavailable("days_to_earnings", "Days to earnings", "days", "earnings date unknown")
    value = float(ctx.days_to_earnings)
    return Indicator(
        key="days_to_earnings",
        label="Days to earnings",
        value=value,
        unit="days",
        signal=_signal(ctx.profile, "days_to_earnings", value),
        detail=f"next earnings in {ctx.days_to_earnings} days",
    )


def _trend_50d(ctx: IndicatorContext) -> Indicator:
    def compute(series: pd.Series) -> Indicator:
        sma = float(series.iloc[-50:].mean())
        value = _round(series.iloc[-1] / sma - 1)
        return Indicator(
            key="trend_50d",
            label="Price vs 50d trend",
            value=value,
            unit="pct",
            signal=_signal(ctx.profile, "trend_50d", value),
            detail=f"{value:+.1%} vs 50d moving average",
        )

    return _price_indicator("trend_50d", "Price vs 50d trend", "pct", ctx.close, 50, compute)


def _avg_dollar_volume_20d(ctx: IndicatorContext) -> Indicator:
    key = "avg_dollar_volume_20d"
    label = "Avg daily dollar volume (20d)"
    if ctx.close is None or ctx.volume is None:
        return _unavailable(key, label, "usd", "needs price and volume history")
    aligned = pd.concat([ctx.close.rename("close"), ctx.volume.rename("volume")], axis=1).dropna()
    if len(aligned) < 20:
        return _unavailable(key, label, "usd", "needs at least 20 aligned price and volume rows")
    value = _round(float((aligned["close"].iloc[-20:] * aligned["volume"].iloc[-20:]).mean()))
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="usd",
        signal=_signal(ctx.profile, key, value),
        detail=f"${value:,.0f} average daily dollar volume over 20d",
    )


def _relative_volume(ctx: IndicatorContext) -> Indicator:
    key = "relative_volume"
    label = "Relative volume (today vs 20d)"
    if ctx.volume is None:
        return _unavailable(key, label, "ratio", "needs volume history")
    volume = ctx.volume.dropna()
    if len(volume) < 21:
        return _unavailable(key, label, "ratio", "needs at least 21 volume rows")
    denominator = float(volume.iloc[-21:-1].mean())
    if denominator == 0:
        return _unavailable(key, label, "ratio", "20d average volume is zero")
    value = _round(float(volume.iloc[-1]) / denominator)
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="ratio",
        signal=_signal(ctx.profile, key, value),
        detail=f"{value:.2f}x today's volume vs prior 20d average",
    )


def _zero_volume_days_90d(ctx: IndicatorContext) -> Indicator:
    key = "zero_volume_days_90d"
    label = "Zero-volume days (90d)"
    if ctx.volume is None:
        return _unavailable(key, label, "count", "needs volume history")
    volume = ctx.volume.dropna()
    if len(volume) < 90:
        return _unavailable(key, label, "count", "needs at least 90 volume rows")
    value = float(int((volume.iloc[-90:] == 0).sum()))
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="count",
        signal=_signal(ctx.profile, key, value),
        detail=f"{int(value)} zero-volume days over 90d",
    )


def _share_dilution(ctx: IndicatorContext) -> Indicator:
    key = "share_dilution"
    label = "Share dilution (period over period)"
    fin = ctx.data.financials
    if fin is None:
        return _unavailable(key, label, "pct", "financials unavailable")
    if fin.shares_outstanding is None:
        return _unavailable(key, label, "pct", "shares outstanding unavailable")
    if fin.shares_outstanding_prior is None:
        return _unavailable(key, label, "pct", "prior shares outstanding unavailable")
    if fin.shares_outstanding_prior <= 0:
        return _unavailable(key, label, "pct", "prior shares outstanding must be positive")

    value = _round(fin.shares_outstanding / fin.shares_outstanding_prior - 1)
    prior = fin.prior_period or "prior period"
    current = fin.fiscal_period or "current period"
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="pct",
        signal=_signal(ctx.profile, key, value),
        detail=f"{value:+.1%} shares outstanding from {prior} to {current}",
    )


def _cash_runway_months(ctx: IndicatorContext) -> Indicator:
    key = "cash_runway_months"
    label = "Cash runway"
    fin = ctx.data.financials
    if fin is None:
        return _unavailable(key, label, "ratio", "financials unavailable")
    if fin.cash_and_equivalents is None:
        return _unavailable(key, label, "ratio", "cash unavailable")
    if fin.operating_cash_flow is None:
        return _unavailable(key, label, "ratio", "operating cash flow unavailable")
    if fin.operating_cash_flow >= 0:
        return Indicator(
            key=key,
            label=label,
            value=None,
            unit="ratio",
            signal="bullish",
            detail="operating cash flow positive — self-funding",
        )
    value = _round(fin.cash_and_equivalents / (abs(fin.operating_cash_flow) / 12))
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="ratio",
        signal=_signal(ctx.profile, key, value),
        detail=f"{value:.1f} months cash runway",
    )


def _filing_recency_days(ctx: IndicatorContext) -> Indicator:
    key = "filing_recency_days"
    label = "Days since last SEC period end"
    fin = ctx.data.financials
    if fin is None or not fin.filed:
        return _unavailable(key, label, "days", "SEC filing date unavailable")
    try:
        filed = pd.Timestamp(fin.filed)
    except Exception:
        return _unavailable(key, label, "days", "SEC filing date unparseable")
    if pd.isna(filed):
        return _unavailable(key, label, "days", "SEC filing date unparseable")
    if filed.tzinfo is not None:
        filed = filed.tz_convert(UTC)
    value = float((datetime.now(UTC).date() - filed.date()).days)
    return Indicator(
        key=key,
        label=label,
        value=value,
        unit="days",
        signal=_signal(ctx.profile, key, value),
        detail=f"{int(value)} days since last SEC period end",
    )


def _float_shares(ctx: IndicatorContext) -> Indicator:
    key = "float_shares"
    label = "Float"
    value = ctx.data.fundamentals.float_shares
    if value is None:
        return _unavailable(key, label, "count", "float shares unavailable")
    rounded = _round(value)
    return Indicator(
        key=key,
        label=label,
        value=rounded,
        unit="count",
        signal=_signal(ctx.profile, key, rounded),
        detail=f"{rounded / 1_000_000:.1f}M float shares",
    )


BUILDERS: dict[str, Builder] = {
    "momentum_12_1": _momentum_12_1,
    "momentum_6m": _momentum_6m,
    "pct_from_52w_high": _pct_from_52w_high,
    "trend_200d": _trend_200d,
    "realized_vol_90d": _realized_vol_90d,
    "beta_1y": _beta_1y,
    "max_drawdown_1y": _max_drawdown_1y,
    "earnings_yield": _earnings_yield,
    "fcf_yield": _fcf_yield,
    "profit_margin": _profit_margin,
    "debt_to_assets": _debt_to_assets,
    "days_to_earnings": _days_to_earnings,
    "trend_50d": _trend_50d,
    "avg_dollar_volume_20d": _avg_dollar_volume_20d,
    "relative_volume": _relative_volume,
    "zero_volume_days_90d": _zero_volume_days_90d,
    "share_dilution": _share_dilution,
    "cash_runway_months": _cash_runway_months,
    "filing_recency_days": _filing_recency_days,
    "float_shares": _float_shares,
}


def compute_indicators(ctx: IndicatorContext) -> list[Indicator]:
    """Compute the active profile's indicators, marking missing inputs unavailable."""
    return [BUILDERS[key](ctx) for key in ctx.profile.indicator_keys]


def _scorecard(symbol: str, indicators: list[Indicator], profile: Profile) -> IndicatorScorecard:
    counts = {
        "bullish": sum(i.signal == "bullish" for i in indicators),
        "bearish": sum(i.signal == "bearish" for i in indicators),
        "neutral": sum(i.signal == "neutral" for i in indicators),
        "unavailable": sum(i.signal == "unavailable" for i in indicators),
    }
    complete = sum(i.value is not None for i in indicators) / len(indicators)
    return IndicatorScorecard(
        symbol=symbol,
        asof=datetime.now(UTC).isoformat(),
        profile=profile.key,
        indicators=indicators,
        data_completeness=_round(complete),
        **counts,
    )


def _extract_column(raw: pd.DataFrame | None, column: str, symbols: list[str]) -> dict[str, pd.Series]:
    if raw is None or raw.empty or column not in raw:
        return {}
    values = raw[column]
    if isinstance(values, pd.Series):
        cleaned = values.dropna()
        return {symbols[0]: cleaned} if not cleaned.empty else {}
    values = values.rename(columns=lambda c: str(c).upper())
    return {
        str(c).upper(): values[c].dropna()
        for c in values.columns
        if not values[c].dropna().empty
    }


def _fetch_history(symbol: str) -> dict[str, dict[str, pd.Series]]:
    symbols = [symbol, "SPY"] if symbol != "SPY" else [symbol]
    start = (date.today() - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat()
    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    volume = _extract_column(raw, "Volume", symbols)
    return {
        "close": _extract_column(raw, "Close", symbols),
        "volume": {symbol: volume[symbol]} if symbol in volume else {},
    }


def _calendar_value(calendar: Any, *keys: str) -> Any:
    if calendar is None:
        return None
    for key in keys:
        if isinstance(calendar, dict) and key in calendar:
            return calendar[key]
        if hasattr(calendar, "loc") and key in getattr(calendar, "index", []):
            value = calendar.loc[key]
            if hasattr(value, "iloc"):
                return value.iloc[0]
            return value
    return None


def _fetch_days_to_earnings(symbol: str) -> int | None:
    try:
        raw = _calendar_value(
            yf.Ticker(symbol).calendar,
            "Earnings Date",
            "Earnings Date Start",
            "Earnings Date End",
        )
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)) and raw:
            raw = raw[0]
        ts = pd.Timestamp(raw)
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        today = pd.Timestamp(datetime.now(UTC).date())
        return int((ts.normalize() - today).days)
    except Exception:
        return None


async def compute_scorecard(
    symbol: str,
    data: TickerData,
    *,
    profile: Profile = LARGECAP,
    fresh: bool = False,
) -> IndicatorScorecard:
    """Fetch price history (+ SPY, + earnings date) and compute the scorecard,
    cached per symbol/profile/day (namespace ``scorecard``, 24h TTL)."""
    sym = symbol.upper()
    today = datetime.now(UTC).date().isoformat()
    key = f"{sym}:{profile.key}:{today}"

    async def produce() -> dict:
        history: dict[str, dict[str, pd.Series]] = {"close": {}, "volume": {}}
        try:
            history = await asyncio.to_thread(_fetch_history, sym)
        except Exception:
            history = {"close": {}, "volume": {}}
        days_to_earnings = await asyncio.to_thread(_fetch_days_to_earnings, sym)
        ctx = IndicatorContext(
            close=history["close"].get(sym),
            volume=history["volume"].get(sym),
            spy_close=history["close"].get("SPY"),
            data=data,
            days_to_earnings=days_to_earnings,
            profile=profile,
        )
        indicators = compute_indicators(ctx)
        return _scorecard(sym, indicators, profile).model_dump()

    value, _ = await with_cache("scorecard", key, HISTORY_TTL_MS, produce, fresh=fresh)
    return IndicatorScorecard.model_validate(value)
