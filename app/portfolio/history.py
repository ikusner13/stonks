from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

MIN_HISTORY_ROWS = 60
MIN_COVERAGE = 0.5


class NoDataError(ValueError):
    """Raised when no requested symbol returns usable price history."""


def drop_short_history(close: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop tickers with too little history before dropping remaining NaN rows."""
    close = close.dropna(axis=1, how="all")
    if close.empty or close.shape[1] == 0:
        return close.dropna(axis=0, how="any"), []

    counts = close.count()
    threshold = max(MIN_HISTORY_ROWS, int(MIN_COVERAGE * counts.max()))
    keep = counts[counts >= threshold].index
    excluded = [str(s) for s in counts[counts < threshold].index]
    clean = close.loc[:, keep].dropna(axis=0, how="any")
    return clean, excluded


def fetch_price_history(symbols: list[str], lookback_days: int) -> tuple[pd.DataFrame, list[str]]:
    """Fetch adjusted close prices and exclude tickers with too little history."""
    uniq = list(dict.fromkeys(s.upper() for s in symbols))
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    raw = yf.download(
        uniq, start=start, auto_adjust=True, progress=False, group_by="column"
    )
    if raw is None or raw.empty:
        raise NoDataError("yfinance returned no data")

    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(uniq[0])
    close = close.rename(columns=lambda c: str(c).upper())

    clean, excluded = drop_short_history(close)
    if clean.shape[0] < 2 or clean.shape[1] == 0:
        raise NoDataError("insufficient overlapping price history")
    return clean, excluded
