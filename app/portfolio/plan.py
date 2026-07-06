"""User-owned target allocations and deterministic rebalance planning."""

from __future__ import annotations

from math import isfinite

from pydantic import BaseModel, Field, field_validator

from ..db import connect
from .decision_support import DRIFT_THRESHOLD, RELATIVE_DRIFT_THRESHOLD, drift_is_significant
from .holdings import PortfolioValuation


class Target(BaseModel):
    symbol: str
    target_weight: float

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        return symbol


class RebalanceItem(BaseModel):
    symbol: str
    price: float | None
    current_weight: float
    target_weight: float
    drift: float
    action: str
    delta_usd: float
    delta_shares: float | None
    after_weight: float


class RebalancePlan(BaseModel):
    base_value: float
    cash_now: float
    cash_after: float
    cash_target_weight: float
    items: list[RebalanceItem]
    untargeted: list[str]
    threshold: float = Field(default=DRIFT_THRESHOLD)
    relative_threshold: float = Field(default=RELATIVE_DRIFT_THRESHOLD)


class ContributionItem(BaseModel):
    symbol: str
    price: float | None
    current_weight: float
    target_weight: float
    buy_usd: float
    buy_shares: float | None
    after_weight: float


class ContributionPlan(BaseModel):
    contribution: float
    base_after: float
    leftover_cash: float
    items: list[ContributionItem]


def init_targets_db() -> None:
    """Create the targets table if needed."""
    with connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS targets (
                symbol TEXT PRIMARY KEY,
                target_weight REAL NOT NULL CHECK(target_weight >= 0)
            )
            """
        )


def _normalized_targets(targets: list[Target]) -> list[Target]:
    by_symbol: dict[str, Target] = {}
    for target in targets:
        clean = Target(symbol=target.symbol, target_weight=target.target_weight)
        if (
            clean.target_weight < 0
            or clean.target_weight > 1
            or not isfinite(clean.target_weight)
        ):
            raise ValueError("target weights must be between 0% and 100%")
        by_symbol[clean.symbol] = clean

    total = sum(t.target_weight for t in by_symbol.values())
    if total > 1.0 + 1e-6:
        raise ValueError(f"target weights sum to {total * 100:.0f}%")
    return sorted(by_symbol.values(), key=lambda t: t.symbol)


def list_targets() -> list[Target]:
    """All target allocations, ordered by symbol."""
    with connect() as c:
        rows = c.execute(
            "SELECT symbol, target_weight FROM targets ORDER BY symbol"
        ).fetchall()
    return [
        Target(symbol=row["symbol"], target_weight=row["target_weight"])
        for row in rows
    ]


def set_targets(targets: list[Target]) -> None:
    """Full replacement of stored targets after validating weights."""
    clean_targets = _normalized_targets(targets)
    with connect() as c:
        c.execute("DELETE FROM targets")
        c.executemany(
            "INSERT INTO targets (symbol, target_weight) VALUES (?, ?)",
            [(target.symbol, target.target_weight) for target in clean_targets],
        )


def remove_target(symbol: str) -> None:
    """Delete one target row; no-op when absent."""
    with connect() as c:
        c.execute("DELETE FROM targets WHERE symbol = ?", (symbol.strip().upper(),))


def plan_rebalance(
    valuation: PortfolioValuation, targets: list[Target]
) -> RebalancePlan | None:
    """Build a deterministic rebalance plan without I/O or network access."""
    base_value = valuation.total_with_cash
    target_map = {target.symbol: target.target_weight for target in _normalized_targets(targets)}
    if base_value <= 0 or not target_map:
        return None

    holdings_by_symbol = {holding.symbol: holding for holding in valuation.holdings}
    untargeted = [
        holding.symbol
        for holding in valuation.holdings
        if holding.symbol not in target_map
    ]

    items: list[RebalanceItem] = []
    for symbol in sorted(target_map):
        target_weight = target_map[symbol]
        holding = holdings_by_symbol.get(symbol)
        price = holding.price if holding is not None else None
        market_value = (
            holding.market_value
            if holding is not None and holding.market_value is not None
            else 0.0
        )
        current_weight = market_value / base_value
        drift = current_weight - target_weight

        if not drift_is_significant(drift, target_weight):
            action = "hold"
            delta_usd = 0.0
        else:
            delta_usd = round((target_weight - current_weight) * base_value, 2)
            action = "buy" if delta_usd > 0 else "sell"

        after_weight = current_weight
        if action != "hold":
            after_weight = (market_value + delta_usd) / base_value

        delta_shares = None
        if price is not None and price > 0:
            delta_shares = round(delta_usd / price, 4)

        items.append(
            RebalanceItem(
                symbol=symbol,
                price=price,
                current_weight=current_weight,
                target_weight=target_weight,
                drift=drift,
                action=action,
                delta_usd=delta_usd,
                delta_shares=delta_shares,
                after_weight=after_weight,
            )
        )

    items.sort(key=lambda item: abs(item.drift), reverse=True)
    total_delta = sum(item.delta_usd for item in items)
    target_sum = sum(target_map.values())
    return RebalancePlan(
        base_value=base_value,
        cash_now=valuation.cash,
        cash_after=round(valuation.cash - total_delta, 2),
        cash_target_weight=max(0.0, 1.0 - target_sum),
        items=items,
        untargeted=untargeted,
    )


def _reduce_to_budget(amounts: dict[str, float], budget: float) -> dict[str, float]:
    total = round(sum(amounts.values()), 2)
    overage = round(total - budget, 2)
    if overage <= 0 or not amounts:
        return amounts
    symbol = max(amounts, key=amounts.__getitem__)
    amounts[symbol] = round(max(0.0, amounts[symbol] - overage), 2)
    return amounts


def plan_contribution(
    valuation: PortfolioValuation, targets: list[Target], contribution: float
) -> ContributionPlan | None:
    """Plan buys-only deployment of new contribution cash."""
    base_value = valuation.total_with_cash
    target_map = {target.symbol: target.target_weight for target in _normalized_targets(targets)}
    if contribution <= 0 or base_value <= 0 or not target_map:
        return None

    contribution = round(contribution, 2)
    base_after = base_value + contribution
    holdings_by_symbol = {holding.symbol: holding for holding in valuation.holdings}

    deficits: dict[str, float] = {}
    current_values: dict[str, float] = {}
    for symbol, target_weight in target_map.items():
        holding = holdings_by_symbol.get(symbol)
        market_value = (
            holding.market_value
            if holding is not None and holding.market_value is not None
            else 0.0
        )
        current_values[symbol] = market_value
        deficits[symbol] = max(0.0, target_weight * base_after - market_value)

    total_deficit = sum(deficits.values())
    if total_deficit <= 0:
        return ContributionPlan(
            contribution=contribution,
            base_after=base_after,
            leftover_cash=contribution,
            items=[],
        )

    if total_deficit <= contribution:
        buy_amounts = {
            symbol: round(deficit, 2)
            for symbol, deficit in deficits.items()
            if deficit > 0
        }
    else:
        buy_amounts = {
            symbol: round(contribution * deficit / total_deficit, 2)
            for symbol, deficit in deficits.items()
            if deficit > 0
        }
    buy_amounts = _reduce_to_budget(buy_amounts, contribution)
    buy_amounts = {
        symbol: buy_usd
        for symbol, buy_usd in buy_amounts.items()
        if buy_usd >= 1.0
    }

    items: list[ContributionItem] = []
    for symbol, buy_usd in buy_amounts.items():
        holding = holdings_by_symbol.get(symbol)
        price = holding.price if holding is not None else None
        buy_shares = round(buy_usd / price, 4) if price is not None and price > 0 else None
        current_value = current_values[symbol]
        items.append(
            ContributionItem(
                symbol=symbol,
                price=price,
                current_weight=current_value / base_after,
                target_weight=target_map[symbol],
                buy_usd=buy_usd,
                buy_shares=buy_shares,
                after_weight=(current_value + buy_usd) / base_after,
            )
        )

    items.sort(key=lambda item: item.buy_usd, reverse=True)
    leftover_cash = round(contribution - sum(item.buy_usd for item in items), 2)
    return ContributionPlan(
        contribution=contribution,
        base_after=base_after,
        leftover_cash=max(0.0, leftover_cash),
        items=items,
    )
