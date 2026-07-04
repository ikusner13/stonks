from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd
import yfinance as yf

from ..cache import with_cache
from ..schemas import TickerData
from .schemas import Indicator, IndicatorScorecard, Signal, Unit

HISTORY_LOOKBACK_DAYS = 420
HISTORY_TTL_MS = 24 * 60 * 60_000

_RULES: dict[str, tuple[float, float]] = {
    "momentum_12_1": (0.10, -0.10),
    "momentum_6m": (0.08, -0.08),
    "pct_from_52w_high": (-0.05, -0.20),
    "trend_200d": (0.0, -0.02),
    "realized_vol_90d": (float("nan"), 0.60),
    "max_drawdown_1y": (float("nan"), -0.40),
    "earnings_yield": (0.06, 0.02),
    "fcf_yield": (0.05, 0.01),
    "profit_margin": (0.15, 0.0),
    "debt_to_assets": (0.15, 0.50),
}


def _round(value: float) -> float:
    return round(float(value), 4)


def _unavailable(key: str, label: str, unit: Unit, detail: str) -> Indicator:
    return Indicator(key=key, label=label, value=None, unit=unit, signal="unavailable", detail=detail)


def _signal(key: str, value: float, *, reversed_polarity: bool = False) -> Signal:
    bullish_at, bearish_at = _RULES[key]
    if reversed_polarity:
        if value < bullish_at:
            return "bullish"
        if value > bearish_at:
            return "bearish"
        return "neutral"
    if not math.isnan(bullish_at) and value > bullish_at:
        return "bullish"
    if value < bearish_at:
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


def compute_indicators(
    close: pd.Series | None,
    spy_close: pd.Series | None,
    data: TickerData,
    days_to_earnings: int | None,
) -> list[Indicator]:
    indicators: list[Indicator] = []

    def momentum_12_1(series: pd.Series) -> Indicator:
        value = _round(series.iloc[-21] / series.iloc[-252] - 1)
        return Indicator(
            key="momentum_12_1",
            label="12-1 month momentum",
            value=value,
            unit="pct",
            signal=_signal("momentum_12_1", value),
            detail=f"{value:+.1%} over months -12..-1",
        )

    indicators.append(
        _price_indicator(
            "momentum_12_1", "12-1 month momentum", "pct", close, 252, momentum_12_1
        )
    )

    def momentum_6m(series: pd.Series) -> Indicator:
        value = _round(series.iloc[-1] / series.iloc[-126] - 1)
        return Indicator(
            key="momentum_6m",
            label="6 month momentum",
            value=value,
            unit="pct",
            signal=_signal("momentum_6m", value),
            detail=f"{value:+.1%} over 6 months",
        )

    indicators.append(
        _price_indicator("momentum_6m", "6 month momentum", "pct", close, 126, momentum_6m)
    )

    def pct_from_52w_high(series: pd.Series) -> Indicator:
        window = series.iloc[-252:]
        value = _round(series.iloc[-1] / window.max() - 1)
        window_detail = (
            "52w high" if len(window) >= 252 else f"high over last {len(window)} sessions"
        )
        return Indicator(
            key="pct_from_52w_high",
            label="Distance from 52w high",
            value=value,
            unit="pct",
            signal=_signal("pct_from_52w_high", value),
            detail=f"{value:.1%} from {window_detail}",
        )

    indicators.append(
        _price_indicator(
            "pct_from_52w_high", "Distance from 52w high", "pct", close, 60, pct_from_52w_high
        )
    )

    def trend_200d(series: pd.Series) -> Indicator:
        sma = float(series.iloc[-200:].mean())
        value = _round(series.iloc[-1] / sma - 1)
        return Indicator(
            key="trend_200d",
            label="Price vs 200d trend",
            value=value,
            unit="pct",
            signal=_signal("trend_200d", value),
            detail=f"{value:+.1%} vs 200d moving average",
        )

    indicators.append(_price_indicator("trend_200d", "Price vs 200d trend", "pct", close, 200, trend_200d))

    def realized_vol_90d(series: pd.Series) -> Indicator:
        returns = series.pct_change().dropna().iloc[-90:]
        if returns.empty:
            return _unavailable(
                "realized_vol_90d", "90d realized volatility", "pct", "needs return rows"
            )
        value = _round(float(returns.std()) * math.sqrt(252))
        signal: Signal = "bearish" if value > _RULES["realized_vol_90d"][1] else "neutral"
        return Indicator(
            key="realized_vol_90d",
            label="90d realized volatility",
            value=value,
            unit="pct",
            signal=signal,
            detail=f"{value:.1%} annualized over 90d",
        )

    indicators.append(
        _price_indicator(
            "realized_vol_90d", "90d realized volatility", "pct", close, 90, realized_vol_90d
        )
    )

    if close is None or spy_close is None:
        indicators.append(_unavailable("beta_1y", "1y beta vs SPY", "ratio", "needs symbol and SPY history"))
    else:
        returns = close.dropna().pct_change().dropna()
        spy_returns = spy_close.dropna().pct_change().dropna()
        aligned = pd.concat([returns.rename("symbol"), spy_returns.rename("spy")], axis=1).dropna()
        aligned = aligned.iloc[-252:]
        spy_var = float(aligned["spy"].var()) if len(aligned) else 0.0
        if len(aligned) < 200 or spy_var == 0:
            indicators.append(_unavailable("beta_1y", "1y beta vs SPY", "ratio", "needs 200 aligned return rows"))
        else:
            value = _round(float(np.cov(aligned["symbol"], aligned["spy"])[0, 1]) / spy_var)
            indicators.append(
                Indicator(
                    key="beta_1y",
                    label="1y beta vs SPY",
                    value=value,
                    unit="ratio",
                    signal="neutral",
                    detail=f"beta {value:.2f} vs SPY over 1y",
                )
            )

    def max_drawdown_1y(series: pd.Series) -> Indicator:
        window = series.iloc[-252:]
        value = _round(float((window / window.cummax() - 1).min()))
        signal: Signal = "bearish" if value < _RULES["max_drawdown_1y"][1] else "neutral"
        window_detail = "1y" if len(window) >= 252 else f"last {len(window)} sessions"
        return Indicator(
            key="max_drawdown_1y",
            label="1y max drawdown",
            value=value,
            unit="pct",
            signal=signal,
            detail=f"{value:.1%} max drawdown over {window_detail}",
        )

    indicators.append(_price_indicator("max_drawdown_1y", "1y max drawdown", "pct", close, 60, max_drawdown_1y))

    pe = data.fundamentals.pe_ratio
    if pe is not None and pe > 0:
        value = _round(1 / pe)
        indicators.append(
            Indicator(
                key="earnings_yield",
                label="Earnings yield",
                value=value,
                unit="pct",
                signal=_signal("earnings_yield", value),
                detail=f"{value:.1%} earnings yield from trailing P/E",
            )
        )
    elif data.financials and data.financials.net_income is not None and data.financials.net_income < 0:
        indicators.append(
            Indicator(
                key="earnings_yield",
                label="Earnings yield",
                value=None,
                unit="pct",
                signal="bearish",
                detail="negative trailing earnings",
            )
        )
    else:
        indicators.append(_unavailable("earnings_yield", "Earnings yield", "pct", "trailing P/E unavailable"))

    fin = data.financials
    market_cap = data.fundamentals.market_cap
    if fin and fin.free_cash_flow is not None and market_cap and market_cap > 0:
        value = _round(fin.free_cash_flow / market_cap)
        indicators.append(
            Indicator(
                key="fcf_yield",
                label="Free cash flow yield",
                value=value,
                unit="pct",
                signal=_signal("fcf_yield", value),
                detail=f"{value:.1%} free cash flow yield",
            )
        )
    else:
        indicators.append(_unavailable("fcf_yield", "Free cash flow yield", "pct", "FCF or market cap unavailable"))

    margin = data.fundamentals.profit_margin
    if margin is not None:
        value = _round(margin)
        indicators.append(
            Indicator(
                key="profit_margin",
                label="Profit margin",
                value=value,
                unit="pct",
                signal=_signal("profit_margin", value),
                detail=f"{value:.1%} profit margin",
            )
        )
    else:
        indicators.append(_unavailable("profit_margin", "Profit margin", "pct", "profit margin unavailable"))

    if fin and fin.total_debt is not None and fin.total_assets is not None and fin.total_assets > 0:
        value = _round(fin.total_debt / fin.total_assets)
        indicators.append(
            Indicator(
                key="debt_to_assets",
                label="Debt to assets",
                value=value,
                unit="ratio",
                signal=_signal("debt_to_assets", value, reversed_polarity=True),
                detail=f"{value:.2f} debt/assets",
            )
        )
    else:
        indicators.append(_unavailable("debt_to_assets", "Debt to assets", "ratio", "debt or assets unavailable"))

    if days_to_earnings is None:
        indicators.append(_unavailable("days_to_earnings", "Days to earnings", "days", "earnings date unknown"))
    else:
        value = float(days_to_earnings)
        indicators.append(
            Indicator(
                key="days_to_earnings",
                label="Days to earnings",
                value=value,
                unit="days",
                signal="neutral",
                detail=f"next earnings in {days_to_earnings} days",
            )
        )

    return indicators


def _scorecard(symbol: str, indicators: list[Indicator]) -> IndicatorScorecard:
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
        indicators=indicators,
        data_completeness=_round(complete),
        **counts,
    )


def _extract_close(raw: pd.DataFrame | None, symbols: list[str]) -> dict[str, pd.Series]:
    if raw is None or raw.empty:
        return {}
    close = raw["Close"]
    if isinstance(close, pd.Series):
        return {symbols[0]: close.dropna()}
    close = close.rename(columns=lambda c: str(c).upper())
    return {str(c).upper(): close[c].dropna() for c in close.columns if not close[c].dropna().empty}


def _fetch_history(symbol: str) -> dict[str, pd.Series]:
    symbols = [symbol, "SPY"] if symbol != "SPY" else [symbol]
    start = (date.today() - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat()
    raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    return _extract_close(raw, symbols)


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
    symbol: str, data: TickerData, *, fresh: bool = False
) -> IndicatorScorecard:
    sym = symbol.upper()
    key = f"{sym}:{datetime.now(UTC).date().isoformat()}"

    async def produce() -> dict:
        history: dict[str, pd.Series] = {}
        try:
            history = await asyncio.to_thread(_fetch_history, sym)
        except Exception:
            history = {}
        days_to_earnings = await asyncio.to_thread(_fetch_days_to_earnings, sym)
        indicators = compute_indicators(
            history.get(sym),
            history.get("SPY"),
            data,
            days_to_earnings,
        )
        return _scorecard(sym, indicators).model_dump()

    value, _ = await with_cache("scorecard", key, HISTORY_TTL_MS, produce, fresh=fresh)
    return IndicatorScorecard.model_validate(value)
