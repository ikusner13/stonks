"""Deterministic tax-awareness signals: wash-sale windows and loss-harvest candidates.

Basis is average cost (the app doesn't track lots), so these are approximations -
flags to check before trading, not tax advice.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from .holdings import PortfolioValuation
from .transactions import Transaction

WASH_SALE_DAYS = 30
MIN_HARVEST_LOSS_USD = 100.0
MIN_HARVEST_LOSS_PCT = 0.05

DISCLAIMER = (
    "Educational flags only, not tax advice. Basis is average cost, not per-lot, "
    "so real wash-sale and harvest math can differ - verify against your broker's "
    "lot-level records before acting."
)


class HarvestCandidate(BaseModel):
    symbol: str
    unrealized_pl: float
    unrealized_pct: float
    market_value: float
    wash_sale_risk: bool
    last_buy_date: str | None
    note: str


class RepurchaseFlag(BaseModel):
    symbol: str
    loss_sale_date: str
    repurchase_date: str
    realized_pl: float
    note: str


class TaxSignals(BaseModel):
    harvest_candidates: list[HarvestCandidate]
    repurchase_flags: list[RepurchaseFlag]
    disclaimer: str = DISCLAIMER


def _txn_date(txn: Transaction) -> date | None:
    try:
        return date.fromisoformat(txn.ts)
    except (TypeError, ValueError):
        return None


def _symbol(txn: Transaction) -> str | None:
    return txn.symbol.upper() if txn.symbol else None


def _recent_buy_dates(
    transactions: list[Transaction],
    symbol: str,
    today: date,
) -> list[date]:
    cutoff = today - timedelta(days=WASH_SALE_DAYS)
    dates: list[date] = []
    for txn in transactions:
        if txn.side != "buy" or _symbol(txn) != symbol:
            continue
        txn_date = _txn_date(txn)
        if txn_date is not None and txn_date >= cutoff:
            dates.append(txn_date)
    return sorted(dates)


def _harvest_candidates(
    valuation: PortfolioValuation,
    transactions: list[Transaction],
    today: date,
) -> list[HarvestCandidate]:
    candidates: list[HarvestCandidate] = []
    for holding in valuation.holdings:
        if holding.price is None or holding.avg_cost is None or holding.avg_cost <= 0:
            continue

        unrealized_pl = (holding.price - holding.avg_cost) * holding.shares
        unrealized_pct = (holding.price - holding.avg_cost) / holding.avg_cost
        if (
            unrealized_pl > -MIN_HARVEST_LOSS_USD
            or unrealized_pct > -MIN_HARVEST_LOSS_PCT
        ):
            continue

        recent_buys = _recent_buy_dates(transactions, holding.symbol, today)
        last_buy_date = recent_buys[-1].isoformat() if recent_buys else None
        if last_buy_date is not None:
            note = (
                f"Selling now would likely trigger a wash sale (bought {last_buy_date}); "
                "the loss would be disallowed. Waiting past "
                f"{WASH_SALE_DAYS}-day window or selling a different lot avoids it."
            )
        else:
            note = (
                f"Unrealized loss of ${abs(unrealized_pl):,.2f} "
                f"({abs(unrealized_pct) * 100:.1f}%). Selling would realize the loss "
                "for tax purposes - only worth doing if it fits your strategy; you "
                f"can't rebuy within {WASH_SALE_DAYS} days without a wash sale."
            )

        candidates.append(
            HarvestCandidate(
                symbol=holding.symbol,
                unrealized_pl=unrealized_pl,
                unrealized_pct=unrealized_pct,
                market_value=holding.market_value or 0.0,
                wash_sale_risk=last_buy_date is not None,
                last_buy_date=last_buy_date,
                note=note,
            )
        )

    return sorted(candidates, key=lambda candidate: candidate.unrealized_pl)


def _repurchase_flags(transactions: list[Transaction]) -> list[RepurchaseFlag]:
    buys_by_symbol: dict[str, list[tuple[date, Transaction]]] = {}
    for txn in transactions:
        if txn.side != "buy":
            continue
        symbol = _symbol(txn)
        txn_date = _txn_date(txn)
        if symbol is not None and txn_date is not None:
            buys_by_symbol.setdefault(symbol, []).append((txn_date, txn))

    for buys in buys_by_symbol.values():
        buys.sort(key=lambda item: item[0])

    flags: list[RepurchaseFlag] = []
    for txn in transactions:
        if txn.side != "sell" or txn.realized_pl is None or txn.realized_pl >= 0:
            continue
        symbol = _symbol(txn)
        sell_date = _txn_date(txn)
        if symbol is None or sell_date is None:
            continue

        for buy_date, _buy_txn in buys_by_symbol.get(symbol, []):
            days_after_sale = (buy_date - sell_date).days
            if 0 <= days_after_sale <= WASH_SALE_DAYS:
                flags.append(
                    RepurchaseFlag(
                        symbol=symbol,
                        loss_sale_date=sell_date.isoformat(),
                        repurchase_date=buy_date.isoformat(),
                        realized_pl=txn.realized_pl,
                        note=(
                            f"Bought {symbol} back {days_after_sale} days after selling at "
                            f"a loss - the ${abs(txn.realized_pl):,.2f} loss is likely "
                            "wash-sale disallowed and added to the new basis."
                        ),
                    )
                )
                break

    return flags


def compute_tax_signals(
    valuation: PortfolioValuation,
    transactions: list[Transaction],
    today: date,
) -> TaxSignals:
    """Pure, no I/O - trivially testable."""
    return TaxSignals(
        harvest_candidates=_harvest_candidates(valuation, transactions, today),
        repurchase_flags=_repurchase_flags(transactions),
    )
