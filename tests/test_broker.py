from __future__ import annotations

from datetime import UTC, datetime
from sqlite3 import IntegrityError

import pytest

from app import config, db
from app.broker.reconcile import diff_holdings, map_activities
from app.broker.snaptrade import BrokerActivity, BrokerPosition, BrokerSnapshot
from app.portfolio import holdings as holdings_mod
from app.portfolio import transactions as txns
from app.portfolio.holdings import Holding, list_holdings, upsert_holding
from app.portfolio.transactions import (
    Transaction,
    list_transactions,
    record_transaction_ledger_only,
)


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    holdings_mod.init_holdings_db()
    txns.init_transactions_db()


def _snapshot(*positions: BrokerPosition, cash: float = 0.0) -> BrokerSnapshot:
    return BrokerSnapshot(
        account_id="acct-1",
        positions=list(positions),
        cash=cash,
        asof=datetime.now(UTC).isoformat(),
    )


def _activity(
    external_id: str,
    type_: str,
    *,
    symbol: str | None = "AAPL",
    shares: float | None = 2,
    price: float | None = 10,
    amount: float = 20,
) -> BrokerActivity:
    return BrokerActivity(
        external_id=external_id,
        ts="2026-01-02",
        type=type_,
        symbol=symbol,
        shares=shares,
        price=price,
        amount=amount,
        description=f"{type_} desc",
    )


def test_diff_holdings_detects_upserts_removals_cash_and_unchanged():
    local = [
        Holding(symbol="AAA", shares=1, avg_cost=10),
        Holding(symbol="BBB", shares=2, avg_cost=20),
        Holding(symbol="CCC", shares=3, avg_cost=None),
        Holding(symbol="LOCAL", shares=1, avg_cost=1),
    ]
    snapshot = _snapshot(
        BrokerPosition(symbol="AAA", shares=1, avg_cost=10),
        BrokerPosition(symbol="BBB", shares=2.1, avg_cost=20),
        BrokerPosition(symbol="CCC", shares=3, avg_cost=None),
        BrokerPosition(symbol="NEW", shares=4, avg_cost=40),
        cash=123.45,
    )

    diff = diff_holdings(local, 50, snapshot)

    assert diff.to_upsert == [
        BrokerPosition(symbol="BBB", shares=2.1, avg_cost=20),
        BrokerPosition(symbol="NEW", shares=4, avg_cost=40),
    ]
    assert diff.to_remove == ["LOCAL"]
    assert diff.unchanged == 2
    assert diff.cash_before == 50
    assert diff.cash_after == 123.45


def test_diff_holdings_treats_equal_values_within_tolerance_as_unchanged():
    diff = diff_holdings(
        [Holding(symbol="AAA", shares=1.0000001, avg_cost=10.0000001)],
        0,
        _snapshot(BrokerPosition(symbol="AAA", shares=1.0000002, avg_cost=10.0000002)),
    )

    assert diff.to_upsert == []
    assert diff.to_remove == []
    assert diff.unchanged == 1


def test_map_activities_maps_supported_types_and_skips_duplicate_or_unmapped():
    activities = [
        _activity("buy-1", "BUY"),
        _activity("sell-1", "SELL"),
        _activity("div-1", "DIVIDEND", amount=3.21),
        _activity("dep-1", "CONTRIBUTION", symbol=None, shares=None, price=None, amount=100),
        _activity("wd-1", "WITHDRAWAL", symbol=None, shares=None, price=None, amount=25),
        _activity("known", "BUY"),
        _activity("fee-1", "FEE", symbol=None, shares=None, price=None, amount=1),
    ]

    mapped, skipped = map_activities(activities, {"known"})

    assert [txn.side for txn in mapped] == ["buy", "sell", "dividend", "deposit", "withdraw"]
    assert [txn.external_id for txn in mapped] == ["buy-1", "sell-1", "div-1", "dep-1", "wd-1"]
    assert mapped[0].symbol == "AAPL"
    assert mapped[0].shares == 2
    assert mapped[0].price == 10
    assert mapped[2].amount == 3.21
    assert mapped[3].symbol is None
    assert [activity.external_id for activity in skipped] == ["known", "fee-1"]


def test_map_activities_skips_buy_or_sell_without_symbol():
    mapped, skipped = map_activities([_activity("bad", "BUY", symbol=None)], set())

    assert mapped == []
    assert [activity.external_id for activity in skipped] == ["bad"]


def test_record_transaction_ledger_only_does_not_mutate_holdings_or_cash():
    upsert_holding("AAPL", 3, 100)
    db.set_cash(50)

    saved = record_transaction_ledger_only(
        Transaction(
            ts="2026-01-02",
            side="buy",
            symbol="MSFT",
            shares=2,
            price=10,
            amount=999,
            realized_pl=None,
            note="broker import",
        ),
        "snap-1",
    )

    assert saved.id is not None
    assert saved.amount == 20
    assert saved.external_id == "snap-1"
    assert list_holdings() == [Holding(symbol="AAPL", shares=3, avg_cost=100)]
    assert db.get_cash() == 50
    assert list_transactions()[0].external_id == "snap-1"


def test_record_transaction_ledger_only_duplicate_external_id_raises():
    txn = Transaction(
        ts="2026-01-02",
        side="deposit",
        symbol=None,
        shares=None,
        price=None,
        amount=100,
        realized_pl=None,
    )
    record_transaction_ledger_only(txn, "dup")

    with pytest.raises(IntegrityError):
        record_transaction_ledger_only(txn, "dup")


def test_transaction_migration_is_idempotent_and_existing_rows_get_null_external_id():
    txns.init_transactions_db()
    txns.init_transactions_db()

    with db.connect() as c:
        c.execute(
            """
            INSERT INTO transactions (ts, side, amount)
            VALUES ('2026-01-02', 'deposit', 10)
            """
        )

    txns.init_transactions_db()
    rows = list_transactions()

    assert rows[0].external_id is None

