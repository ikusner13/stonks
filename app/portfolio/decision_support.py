"""Decision-support helpers for manual trading: portfolio health, allocation
drift, and position sizing.

Everything here is deterministic and grounded in data we already have — live
holdings valuations, the optimizer's current-vs-optimal weights, and a research
report's stated confidence. No advice, no order placement: these are
educational signals to help a human make a more informed manual decision.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from itertools import combinations
from math import isfinite

import pandas as pd
from pydantic import BaseModel, Field

from ..profiles.base import Profile
from ..profiles.largecap import LARGECAP
from ..cache import with_cache
from ..schemas import Confidence
from .holdings import PortfolioValuation
from .optimize import OptimizeResult

DISCLAIMER = (
    "Decision-support only, not investment advice. These figures are simple "
    "rules of thumb to help you think about risk — always adjust for your own "
    "goals, time horizon, and risk tolerance, and make every trade decision "
    "yourself."
)

# A position whose live weight differs from the optimizer's target by more than
# either band is flagged as "drifted" and worth a second look.
DRIFT_THRESHOLD = 0.05
RELATIVE_DRIFT_THRESHOLD = 0.20

# Two holdings whose daily returns correlate above this are treated as moving
# "together" — a source of hidden concentration the per-name view can't see.
HIGH_CORRELATION = 0.80

# Correlation over a multi-year lookback is stable for a trading day; the cache
# key already pins symbols + lookback + day, so a one-day TTL is plenty.
CORRELATION_TTL_MS = 24 * 60 * 60_000

SHORT_VOL_DAYS = 21
LONG_VOL_MIN_DAYS = 120
ELEVATED_VOL_RATIO = 1.5
CALM_VOL_RATIO = 0.75
REGIME_TTL_MS = 24 * 60 * 60_000
REGIME_LOOKBACK_DAYS = 400  # calendar days fetched, yields ~252 trading rows


def drift_is_significant(drift: float, target: float) -> bool:
    """Dual band: >5 absolute pts, or >20% of a nonzero target weight."""
    if abs(drift) > DRIFT_THRESHOLD:
        return True
    return target > 0 and abs(drift) / target > RELATIVE_DRIFT_THRESHOLD


def _format_points(weight: float) -> str:
    points = abs(weight) * 100
    rounded = round(points, 1)
    if rounded.is_integer():
        return f"{rounded:.0f}"
    return f"{rounded:.1f}"


# --- 1) Portfolio health ----------------------------------------------------


class PortfolioHealth(BaseModel):
    num_holdings: int
    top1_pct: float  # weight of the single largest holding (fraction 0-1)
    top3_pct: float
    top5_pct: float
    top1_symbol: str | None
    concentration_level: str  # "low" | "moderate" | "high"
    diversification_note: str


def assess_portfolio_health(valuation: PortfolioValuation) -> PortfolioHealth | None:
    """Summarize how concentrated the portfolio is, in beginner-friendly terms.

    Returns ``None`` when no holding has a computable weight (e.g. prices
    unavailable), so the caller can show a graceful fallback.
    """
    weighted = [
        (h.symbol, h.weight) for h in valuation.holdings if h.weight is not None
    ]
    if not weighted:
        return None

    weighted.sort(key=lambda x: x[1], reverse=True)
    weights = [w for _, w in weighted]
    n = len(weighted)

    top1 = sum(weights[:1])
    top3 = sum(weights[:3])
    top5 = sum(weights[:5])

    # Concentration is judged on both the single largest bet and how few names
    # carry most of the portfolio. Thresholds are intentionally conservative.
    if n < 3 or top1 >= 0.40 or top3 >= 0.80:
        level = "high"
        note = (
            f"Your portfolio leans heavily on a few positions — the largest "
            f"alone is {top1 * 100:.0f}% of the total. A sharp move in one "
            f"name would swing your whole portfolio. Spreading across more "
            f"positions, or trimming the biggest, would lower that risk."
        )
    elif n < 6 or top1 >= 0.25 or top3 >= 0.60:
        level = "moderate"
        note = (
            f"Reasonably balanced, but somewhat concentrated: your top 3 "
            f"holdings are {top3 * 100:.0f}% of the portfolio. Worth keeping "
            f"an eye on the largest positions as they grow."
        )
    else:
        level = "low"
        note = (
            f"Nicely spread across {n} positions, with no single holding "
            f"dominating (largest is {top1 * 100:.0f}%). Lower single-stock "
            f"risk — though diversification never removes market risk entirely."
        )

    return PortfolioHealth(
        num_holdings=n,
        top1_pct=top1,
        top3_pct=top3,
        top5_pct=top5,
        top1_symbol=weighted[0][0],
        concentration_level=level,
        diversification_note=note,
    )


# --- 2) Allocation drift & rebalancing signals ------------------------------


class DriftItem(BaseModel):
    symbol: str
    current_weight: float  # fraction 0-1
    target_weight: float
    drift: float  # current - target (positive = overweight vs target)
    direction: str  # "over" | "under"
    significant: bool  # dual band: abs drift or relative drift vs target
    suggestion: str


class DriftAnalysis(BaseModel):
    items: list[DriftItem]
    significant_count: int
    threshold_pct: float = DRIFT_THRESHOLD
    relative_threshold_pct: float = RELATIVE_DRIFT_THRESHOLD


def analyze_drift(result: OptimizeResult) -> DriftAnalysis | None:
    """Compare live weights against the optimizer's target weights.

    Returns ``None`` if the optimizer had no current allocation to compare
    against (e.g. holdings carried no value/shares).
    """
    if result.current is None:
        return None

    current = result.current.weights
    target = result.optimal.weights
    symbols = sorted(set(current) | set(target))

    items: list[DriftItem] = []
    for sym in symbols:
        cur = current.get(sym, 0.0)
        tgt = target.get(sym, 0.0)
        drift = cur - tgt
        direction = "over" if drift >= 0 else "under"
        significant = drift_is_significant(drift, tgt)
        relative_triggered = (
            significant
            and abs(drift) <= DRIFT_THRESHOLD
            and tgt > 0
            and abs(drift) / tgt > RELATIVE_DRIFT_THRESHOLD
        )

        if not significant:
            suggestion = "Close to target — no action needed."
        elif direction == "over":
            relative_context = (
                f" ({abs(drift) / tgt:.0%} of its {tgt:.0%} target)"
                if relative_triggered
                else ""
            )
            suggestion = (
                f"Overweight by {_format_points(drift)} pts{relative_context}. Consider trimming "
                f"{sym} to move closer to the suggested allocation."
            )
        else:
            relative_context = (
                f" ({abs(drift) / tgt:.0%} of its {tgt:.0%} target)"
                if relative_triggered
                else ""
            )
            suggestion = (
                f"Underweight by {_format_points(drift)} pts{relative_context}. Consider adding "
                f"to {sym} to move closer to the suggested allocation."
            )

        items.append(
            DriftItem(
                symbol=sym,
                current_weight=cur,
                target_weight=tgt,
                drift=drift,
                direction=direction,
                significant=significant,
                suggestion=suggestion,
            )
        )

    # Biggest absolute drift first — that's where attention is best spent.
    items.sort(key=lambda i: abs(i.drift), reverse=True)
    significant_count = sum(1 for i in items if i.significant)
    return DriftAnalysis(items=items, significant_count=significant_count)


# --- 3) Position sizing guidance --------------------------------------------

class PositionSizeGuidance(BaseModel):
    symbol: str | None
    confidence: Confidence
    portfolio_value: float
    low_pct: float  # fraction of portfolio
    high_pct: float
    low_dollars: float
    high_dollars: float
    note: str
    current_weight: float | None = None
    already_at_band: bool = False
    profile: str = "largecap"
    liquidity_cap_dollars: float | None = None


def suggest_position_size(
    portfolio_value: float,
    confidence: Confidence,
    symbol: str | None = None,
    *,
    current_weight: float | None = None,
    profile: Profile = LARGECAP,
    adv_dollars: float | None = None,
) -> PositionSizeGuidance:
    """Suggest a starting position-size *range* for a candidate.

    Logic is intentionally simple: take a conservative band of the portfolio,
    widen it slightly when the research confidence is higher. Shown as both a
    percentage and an approximate dollar amount.
    """
    low_pct, high_pct = profile.sizing_bands[confidence]
    low_headroom = low_pct
    high_headroom = high_pct
    already_at_band = False
    liquidity_cap_dollars: float | None = None
    note = (
        f"Based on '{confidence}' research confidence, a starting position of "
        f"roughly {low_pct * 100:.1f}–{high_pct * 100:.0f}% of your "
        f"${portfolio_value:,.0f} portfolio is a conservative range to "
        f"consider. Higher-confidence research earns a slightly larger band; "
        f"size down if you're unsure. This is guidance, not advice."
    )
    if current_weight is not None:
        low_headroom = max(0.0, low_pct - current_weight)
        high_headroom = max(0.0, high_pct - current_weight)
        if current_weight >= high_pct:
            already_at_band = True
            note = (
                f"The existing position is already {current_weight:.1%} of the portfolio, "
                f"at/above the {high_pct:.0%} band for this confidence — adding would "
                f"increase concentration rather than diversify."
            )
        elif current_weight > 0:
            note = (
                f"The existing {current_weight:.1%} position counts toward the "
                f"{low_pct * 100:.1f}–{high_pct * 100:.0f}% band for '{confidence}' "
                f"confidence, so the dollar range shown is remaining headroom. This is "
                f"guidance, not advice."
            )
    if portfolio_value <= 0:
        note = (
            "Add holdings so the app knows your portfolio size, then it can "
            "suggest an approximate dollar range to consider for a new position."
        )

    low_dollars = portfolio_value * low_headroom
    high_dollars = portfolio_value * high_headroom
    liquidity = profile.liquidity_sizing
    if liquidity is not None and adv_dollars is not None:
        liquidity_cap_dollars = (
            adv_dollars * liquidity.max_participation * liquidity.max_days_to_exit
        )
        cap_binds = liquidity_cap_dollars < high_dollars
        low_dollars = min(low_dollars, liquidity_cap_dollars)
        high_dollars = min(high_dollars, liquidity_cap_dollars)
        if cap_binds:
            note += (
                f" Liquidity cap applied: sized so the position could be exited in "
                f"~{liquidity.max_days_to_exit} days at "
                f"{liquidity.max_participation:.0%} of average daily dollar volume."
            )
    elif liquidity is not None:
        note += (
            " Liquidity is unknown for this profile; treat the upper band with caution."
        )

    return PositionSizeGuidance(
        symbol=symbol,
        confidence=confidence,
        portfolio_value=portfolio_value,
        low_pct=low_pct,
        high_pct=high_pct,
        low_dollars=low_dollars,
        high_dollars=high_dollars,
        note=note,
        current_weight=current_weight,
        already_at_band=already_at_band,
        profile=profile.key,
        liquidity_cap_dollars=liquidity_cap_dollars,
    )


# --- 4) Correlation / "hidden" concentration --------------------------------
#
# Counting weight by individual stock misses the case where several holdings
# move together (same sector, same macro driver). Pairwise return correlation
# measures that directly: a basket of names that all rise and fall in lockstep
# is less diversified than its position count suggests.


class CorrelatedPair(BaseModel):
    symbol_a: str
    symbol_b: str
    correlation: float  # -1..1


class CorrelationInsight(BaseModel):
    symbols: list[str]
    avg_correlation: float  # mean of all distinct pairwise correlations
    high_pairs: list[CorrelatedPair]  # pairs above HIGH_CORRELATION
    level: str  # "low" | "moderate" | "high" hidden concentration
    note: str
    threshold: float = HIGH_CORRELATION
    excluded_symbols: list[str] = Field(default_factory=list)
    sample_days: int = 0
    matrix: dict[str, dict[str, float]] | None = None


def analyze_correlation(
    matrix: dict[str, dict[str, float]],
) -> CorrelationInsight | None:
    """Summarize how much a portfolio's holdings move together.

    ``matrix`` is a correlation matrix as nested dicts (symbol -> symbol ->
    correlation). Pure and network-free so it's trivially testable; the price
    fetch lives in :func:`compute_correlation_insight`. Returns ``None`` if
    there are fewer than two symbols to compare.
    """
    symbols = list(matrix)
    if len(symbols) < 2:
        return None

    pairs: list[CorrelatedPair] = []
    for a, b in combinations(symbols, 2):
        corr = matrix.get(a, {}).get(b)
        if corr is None:
            continue
        pairs.append(CorrelatedPair(symbol_a=a, symbol_b=b, correlation=round(corr, 3)))
    if not pairs:
        return None

    avg = sum(p.correlation for p in pairs) / len(pairs)
    high_pairs = sorted(
        (p for p in pairs if p.correlation >= HIGH_CORRELATION),
        key=lambda p: p.correlation,
        reverse=True,
    )

    if avg >= 0.70 or len(high_pairs) >= 3:
        level = "high"
        note = (
            f"Several of your holdings tend to rise and fall together (average "
            f"correlation {avg:.2f}). Your portfolio is less diversified than "
            f"its {len(symbols)} positions suggest — in a downturn these names "
            f"could drop at the same time. Adding something that behaves "
            f"differently (a different sector, or an asset like bonds) would "
            f"diversify more effectively than another correlated name."
        )
    elif avg >= 0.40 or high_pairs:
        level = "moderate"
        note = (
            f"Your holdings move together a moderate amount (average "
            f"correlation {avg:.2f}). You get some diversification benefit, but "
            f"keep an eye on the closely-linked pairs below before adding to "
            f"either side of them."
        )
    else:
        level = "low"
        note = (
            f"Your holdings move fairly independently (average correlation "
            f"{avg:.2f}) — a sign of genuine diversification, since they're "
            f"unlikely to all fall at once."
        )

    return CorrelationInsight(
        symbols=symbols,
        avg_correlation=round(avg, 3),
        high_pairs=high_pairs,
        level=level,
        note=note,
        matrix={
            a: {
                b: round(matrix[a][b], 2)
                for b in symbols
                if b in matrix.get(a, {}) and isfinite(matrix[a][b])
            }
            for a in symbols
        },
    )


def _correlation_sync(symbols: list[str], lookback_days: int) -> CorrelationInsight | None:
    # Reuse the performance module's return-history fetch, then correlate.
    from .performance import _fetch_returns
    from .history import MIN_HISTORY_ROWS

    uniq = list(dict.fromkeys(s.upper() for s in symbols))
    if len(uniq) < 2:
        return None
    returns, excluded = _fetch_returns(uniq, lookback_days)
    if returns is None or returns.shape[0] < MIN_HISTORY_ROWS or returns.shape[1] < 2:
        return None
    corr = returns.corr()
    matrix = {
        a: {b: float(corr.loc[a, b]) for b in corr.columns} for a in corr.index
    }
    insight = analyze_correlation(matrix)
    if insight is None:
        return None
    return insight.model_copy(update={"excluded_symbols": excluded, "sample_days": returns.shape[0]})


async def compute_correlation_insight(
    symbols: list[str], lookback_days: int = 730, *, fresh: bool = False
) -> CorrelationInsight | None:
    """Fetch return history for ``symbols`` and summarize how they co-move.

    Cached per symbol-set + lookback + trading day, so revisiting the portfolio
    page skips the yfinance download. Blocking work is offloaded to a thread,
    mirroring :func:`app.portfolio.performance.compute_performance`.
    """
    uniq = list(dict.fromkeys(s.upper() for s in symbols))
    if len(uniq) < 2:
        return None

    day = datetime.now(UTC).date().isoformat()
    key = f"{'-'.join(sorted(uniq))}:{lookback_days}:{day}"

    async def produce() -> dict | None:
        insight = await asyncio.to_thread(_correlation_sync, uniq, lookback_days)
        return insight.model_dump() if insight else None

    # A "no data" result serializes to None, which the cache treats as a miss —
    # so we never persist an empty insight and will retry it next time.
    value, _ = await with_cache("correlation", key, CORRELATION_TTL_MS, produce, fresh=fresh)
    return CorrelationInsight.model_validate(value) if value is not None else None


# --- 5) Volatility regime ----------------------------------------------------
#
# This compares the portfolio's recent realized volatility with its own longer
# history. It is not a market-volatility indicator.


class RegimeSignal(BaseModel):
    short_vol: float  # annualized fraction, last SHORT_VOL_DAYS trading days
    long_vol: float  # annualized fraction, full sample
    vol_ratio: float  # short / long
    level: str  # "calm" | "normal" | "elevated"
    note: str
    sample_days: int
    asof: str


def analyze_regime(portfolio_returns: pd.Series) -> RegimeSignal | None:
    """Pure + network-free volatility-regime summary for a portfolio return series."""
    returns = portfolio_returns.dropna()
    if len(returns) < LONG_VOL_MIN_DAYS:
        return None

    short_vol = float(returns.tail(SHORT_VOL_DAYS).std() * (252 ** 0.5))
    long_vol = float(returns.std() * (252 ** 0.5))
    if not isfinite(long_vol) or long_vol == 0:
        return None
    if not isfinite(short_vol):
        return None

    ratio = short_vol / long_vol
    if ratio >= ELEVATED_VOL_RATIO:
        level = "elevated"
        note = (
            f"Your portfolio's recent day-to-day swings are about {ratio:.1f}x "
            f"its own longer-term norm — risk is running hotter than usual. A "
            f"cautious time to add to positions; drops can be sharper in "
            f"stretches like this. This compares the portfolio with its own "
            f"history, not the market."
        )
    elif ratio <= CALM_VOL_RATIO:
        level = "calm"
        note = (
            f"Your portfolio's recent day-to-day swings are about {ratio:.1f}x "
            f"its own longer-term norm — calmer than usual. This compares the "
            f"portfolio with its own history, not the market, so keep position "
            f"risk in mind even when the tape feels quiet."
        )
    else:
        level = "normal"
        note = (
            f"Your portfolio's recent day-to-day swings are about {ratio:.1f}x "
            f"its own longer-term norm — roughly normal for this portfolio. "
            f"This is a self-history check, not a market volatility forecast."
        )

    return RegimeSignal(
        short_vol=short_vol,
        long_vol=long_vol,
        vol_ratio=ratio,
        level=level,
        note=note,
        sample_days=len(returns),
        asof=datetime.now(UTC).isoformat(),
    )


def _regime_sync(weights: dict[str, float]) -> RegimeSignal | None:
    from .performance import _build_portfolio_returns

    portfolio, _ = _build_portfolio_returns(weights, REGIME_LOOKBACK_DAYS)
    if portfolio is None:
        return None
    return analyze_regime(portfolio)


async def compute_regime_signal(
    weights: dict[str, float], *, fresh: bool = False
) -> RegimeSignal | None:
    """Fetch portfolio returns and summarize recent volatility versus its own history."""
    clean_weights = {
        symbol.upper(): round(weight, 6)
        for symbol, weight in weights.items()
        if weight is not None and weight > 0
    }
    if not clean_weights:
        return None

    day = datetime.now(UTC).date().isoformat()
    key_parts = [f"{symbol}:{clean_weights[symbol]:.6f}" for symbol in sorted(clean_weights)]
    key = f"{'-'.join(key_parts)}:{day}"

    async def produce() -> dict | None:
        signal = await asyncio.to_thread(_regime_sync, clean_weights)
        return signal.model_dump() if signal else None

    value, _ = await with_cache("regime", key, REGIME_TTL_MS, produce, fresh=fresh)
    return RegimeSignal.model_validate(value) if value is not None else None
