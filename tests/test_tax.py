from __future__ import annotations

from datetime import date

from app.portfolio.holdings import HoldingValuation, PortfolioValuation
from app.portfolio.tax import WASH_SALE_DAYS, compute_tax_signals
from app.portfolio.transactions import Transaction


TODAY = date(2026, 7, 5)


def _holding(
    symbol: str,
    *,
    shares: float = 10,
    avg_cost: float | None = 100,
    price: float | None = 80,
) -> HoldingValuation:
    market_value = shares * price if price is not None else None
    cost_value = shares * avg_cost if avg_cost is not None else None
    unrealized_pl = (
        market_value - cost_value
        if market_value is not None and cost_value is not None
        else None
    )
    unrealized_pct = (
        unrealized_pl / cost_value
        if unrealized_pl is not None and cost_value not in (None, 0)
        else None
    )
    return HoldingValuation(
        symbol=symbol,
        shares=shares,
        avg_cost=avg_cost,
        price=price,
        market_value=market_value,
        cost_value=cost_value,
        unrealized_pl=unrealized_pl,
        unrealized_pl_pct=unrealized_pct,
        weight=None,
    )


def _valuation(holdings: list[HoldingValuation]) -> PortfolioValuation:
    return PortfolioValuation(
        holdings=holdings,
        total_value=0,
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof=TODAY.isoformat(),
    )


def _txn(
    ts: str,
    side: str,
    *,
    symbol: str | None = None,
    realized_pl: float | None = None,
) -> Transaction:
    return Transaction(
        ts=ts,
        side=side,
        symbol=symbol,
        shares=1,
        price=1,
        amount=1,
        realized_pl=realized_pl,
    )


def test_harvest_candidate_requires_dollar_and_percent_thresholds():
    signals = compute_tax_signals(
        _valuation([
            _holding("BIGD", shares=1_000, avg_cost=1.00, price=0.91),
            _holding("BIGP", shares=1, avg_cost=1_000, price=940),
        ]),
        [],
        TODAY,
    )

    assert signals.harvest_candidates == []


def test_qualifying_candidate_without_recent_buy_has_no_wash_sale_risk():
    signals = compute_tax_signals(_valuation([_holding("AAPL")]), [], TODAY)

    assert len(signals.harvest_candidates) == 1
    candidate = signals.harvest_candidates[0]
    assert candidate.symbol == "AAPL"
    assert candidate.unrealized_pl == -200
    assert candidate.unrealized_pct == -0.20
    assert candidate.wash_sale_risk is False
    assert candidate.last_buy_date is None


def test_qualifying_candidate_with_buy_10_days_ago_has_wash_sale_risk():
    signals = compute_tax_signals(
        _valuation([_holding("AAPL")]),
        [_txn("2026-06-25", "buy", symbol="aapl")],
        TODAY,
    )

    candidate = signals.harvest_candidates[0]
    assert candidate.wash_sale_risk is True
    assert candidate.last_buy_date == "2026-06-25"


def test_recent_buy_window_is_inclusive_at_30_days_and_excludes_31_days():
    inclusive = compute_tax_signals(
        _valuation([_holding("AAPL")]),
        [_txn("2026-06-05", "buy", symbol="AAPL")],
        TODAY,
    )
    excluded = compute_tax_signals(
        _valuation([_holding("AAPL")]),
        [_txn("2026-06-04", "buy", symbol="AAPL")],
        TODAY,
    )

    assert WASH_SALE_DAYS == 30
    assert inclusive.harvest_candidates[0].wash_sale_risk is True
    assert inclusive.harvest_candidates[0].last_buy_date == "2026-06-05"
    assert excluded.harvest_candidates[0].wash_sale_risk is False


def test_repurchase_flag_for_loss_sale_then_rebuy_within_window_only():
    flagged = compute_tax_signals(
        _valuation([]),
        [
            _txn("2026-06-01", "sell", symbol="AAPL", realized_pl=-125),
            _txn("2026-06-06", "buy", symbol="AAPL"),
        ],
        TODAY,
    )
    late = compute_tax_signals(
        _valuation([]),
        [
            _txn("2026-06-01", "sell", symbol="AAPL", realized_pl=-125),
            _txn("2026-07-11", "buy", symbol="AAPL"),
        ],
        TODAY,
    )
    gain = compute_tax_signals(
        _valuation([]),
        [
            _txn("2026-06-01", "sell", symbol="AAPL", realized_pl=125),
            _txn("2026-06-06", "buy", symbol="AAPL"),
        ],
        TODAY,
    )

    assert len(flagged.repurchase_flags) == 1
    flag = flagged.repurchase_flags[0]
    assert flag.symbol == "AAPL"
    assert flag.loss_sale_date == "2026-06-01"
    assert flag.repurchase_date == "2026-06-06"
    assert flag.realized_pl == -125
    assert late.repurchase_flags == []
    assert gain.repurchase_flags == []


def test_holdings_without_price_or_avg_cost_are_skipped_without_error():
    signals = compute_tax_signals(
        _valuation([
            _holding("NOPRICE", price=None),
            _holding("NOCOST", avg_cost=None),
        ]),
        [_txn("not-a-date", "buy", symbol="NOPRICE")],
        TODAY,
    )

    assert signals.harvest_candidates == []
    assert signals.repurchase_flags == []
