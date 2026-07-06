"""Pure broker reconciliation helpers."""

from __future__ import annotations

from pydantic import BaseModel

from ..portfolio.holdings import Holding
from ..portfolio.transactions import Transaction
from .snaptrade import BrokerActivity, BrokerPosition, BrokerSnapshot

ACTIVITY_SIDE_MAP = {
    "BUY": "buy",
    "SELL": "sell",
    "DIVIDEND": "dividend",
    "INTEREST": "dividend",
    # Fidelity reports core money-market sweeps (e.g. SPAXX interest) as REI
    # "reinvestment"; the position itself shows up as cash, so this is income.
    # A true stock DRIP would lose its buy row, but the share change still
    # arrives via the position mirror.
    "REI": "dividend",
    "CONTRIBUTION": "deposit",
    "DEPOSIT": "deposit",
    "EFT_IN": "deposit",
    "WITHDRAWAL": "withdraw",
    "EFT_OUT": "withdraw",
}

_TOLERANCE = 1e-6


class HoldingsDiff(BaseModel):
    to_upsert: list[BrokerPosition]
    to_remove: list[str]
    cash_before: float
    cash_after: float
    unchanged: int


def _avg_cost_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= _TOLERANCE


def diff_holdings(
    local: list[Holding],
    local_cash: float,
    snapshot: BrokerSnapshot,
) -> HoldingsDiff:
    broker_by_symbol = {position.symbol: position for position in snapshot.positions}
    local_by_symbol = {holding.symbol: holding for holding in local}
    to_upsert: list[BrokerPosition] = []
    unchanged = 0

    for symbol, broker_position in broker_by_symbol.items():
        local_position = local_by_symbol.get(symbol)
        if local_position is None:
            to_upsert.append(broker_position)
            continue
        shares_equal = abs(local_position.shares - broker_position.shares) <= _TOLERANCE
        cost_equal = _avg_cost_equal(local_position.avg_cost, broker_position.avg_cost)
        if shares_equal and cost_equal:
            unchanged += 1
        else:
            to_upsert.append(broker_position)

    to_remove = sorted(symbol for symbol in local_by_symbol if symbol not in broker_by_symbol)
    return HoldingsDiff(
        to_upsert=to_upsert,
        to_remove=to_remove,
        cash_before=local_cash,
        cash_after=snapshot.cash,
        unchanged=unchanged,
    )


def map_activities(
    activities: list[BrokerActivity],
    known_external_ids: set[str],
) -> tuple[list[Transaction], list[BrokerActivity]]:
    mapped: list[Transaction] = []
    skipped: list[BrokerActivity] = []

    for activity in activities:
        if activity.external_id in known_external_ids:
            skipped.append(activity)
            continue

        side = ACTIVITY_SIDE_MAP.get(activity.type)
        if side is None:
            skipped.append(activity)
            continue

        if side in {"buy", "sell"}:
            if not activity.symbol or not activity.shares or not activity.price:
                skipped.append(activity)
                continue
            mapped.append(
                Transaction(
                    ts=activity.ts,
                    side=side,
                    symbol=activity.symbol,
                    shares=activity.shares,
                    price=activity.price,
                    amount=activity.amount,
                    realized_pl=None,
                    note=activity.description,
                    external_id=activity.external_id,
                )
            )
            continue

        mapped.append(
            Transaction(
                ts=activity.ts,
                side=side,
                symbol=activity.symbol if side == "dividend" else None,
                shares=None,
                price=None,
                amount=activity.amount,
                realized_pl=None,
                note=activity.description,
                external_id=activity.external_id,
            )
        )

    return mapped, skipped

