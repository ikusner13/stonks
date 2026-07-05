from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isclose

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.portfolio import holdings as holdings_mod
from app.portfolio import transactions as txns
from app.portfolio.holdings import Holding, PortfolioValuation, list_holdings, upsert_holding
from app.portfolio.transactions import (
    Transaction,
    apply_transaction,
    compute_returns,
    list_transactions,
    xirr,
)
from app.schemas import Quote, TickerData
from app.web import app as web_app


def _txn(
    ts: str,
    side: str,
    *,
    symbol: str | None = None,
    shares: float | None = None,
    price: float | None = None,
    amount: float = 0.0,
    note: str = "",
) -> Transaction:
    return Transaction(
        ts=ts,
        side=side,
        symbol=symbol,
        shares=shares,
        price=price,
        amount=amount,
        realized_pl=None,
        note=note,
    )


def _valuation(
    *,
    total_with_cash: float = 11_000,
    unpriced_symbols: list[str] | None = None,
) -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[],
        total_value=max(0.0, total_with_cash - db.get_cash()),
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof=datetime.now(UTC).isoformat(),
        unpriced_symbols=unpriced_symbols or [],
        cash=db.get_cash(),
        total_with_cash=total_with_cash,
        cash_pct=0.0,
    )


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    holdings_mod.init_holdings_db()
    txns.init_transactions_db()


def _txn_count() -> int:
    return len(list_transactions(limit=10_000))


def test_apply_buy_math_fresh_existing_and_unknown_basis():
    db.set_cash(2_000)

    saved = apply_transaction(_txn("2026-01-02", "buy", symbol="aapl", shares=2, price=100))
    assert saved.id is not None
    assert saved.symbol == "AAPL"
    assert saved.amount == 200
    assert db.get_cash() == 1_800
    assert list_holdings() == [Holding(symbol="AAPL", shares=2, avg_cost=100)]

    apply_transaction(_txn("2026-01-03", "buy", symbol="AAPL", shares=3, price=120))
    assert db.get_cash() == 1_440
    assert list_holdings()[0] == Holding(symbol="AAPL", shares=5, avg_cost=112)

    upsert_holding("MSFT", 1, None)
    apply_transaction(_txn("2026-01-04", "buy", symbol="MSFT", shares=1, price=10))
    assert next(h for h in list_holdings() if h.symbol == "MSFT") == Holding(
        symbol="MSFT", shares=2, avg_cost=None
    )


def test_apply_insufficient_cash_writes_nothing():
    db.set_cash(50)
    upsert_holding("AAPL", 1, 100)
    before_cash = db.get_cash()
    before_holdings = list_holdings()
    before_count = _txn_count()

    with pytest.raises(ValueError, match="insufficient cash"):
        apply_transaction(_txn("2026-01-02", "buy", symbol="AAPL", shares=1, price=100))

    assert db.get_cash() == before_cash
    assert list_holdings() == before_holdings
    assert _txn_count() == before_count


def test_apply_oversell_writes_nothing():
    upsert_holding("AAPL", 1, 100)
    db.set_cash(0)

    with pytest.raises(ValueError, match="sell shares exceed held shares"):
        apply_transaction(_txn("2026-01-02", "sell", symbol="AAPL", shares=2, price=120))

    assert db.get_cash() == 0
    assert list_holdings() == [Holding(symbol="AAPL", shares=1, avg_cost=100)]
    assert _txn_count() == 0


def test_apply_sell_realized_pl_and_deletes_zero_position():
    db.set_cash(1_000)
    apply_transaction(_txn("2026-01-02", "buy", symbol="AAPL", shares=2, price=100))

    saved = apply_transaction(_txn("2026-02-02", "sell", symbol="AAPL", shares=2, price=120))

    assert saved.realized_pl == 40
    assert db.get_cash() == 1_040
    assert list_holdings() == []


def test_apply_deposit_withdraw_future_and_non_positive_values():
    apply_transaction(_txn("2026-01-02", "deposit", amount=500))
    assert db.get_cash() == 500

    apply_transaction(_txn("2026-01-03", "withdraw", amount=125.25))
    assert db.get_cash() == 374.75

    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        apply_transaction(_txn(future, "deposit", amount=1))
    with pytest.raises(ValueError, match="amount must be > 0"):
        apply_transaction(_txn("2026-01-04", "deposit", amount=0))
    with pytest.raises(ValueError, match="amount must be > 0"):
        apply_transaction(_txn("2026-01-04", "withdraw", amount=-1))
    with pytest.raises(ValueError, match="shares must be > 0"):
        apply_transaction(_txn("2026-01-04", "buy", symbol="AAPL", shares=0, price=1))
    with pytest.raises(ValueError, match="price must be > 0"):
        apply_transaction(_txn("2026-01-04", "buy", symbol="AAPL", shares=1, price=0))


def test_xirr_known_fixture_and_guards():
    assert xirr([("2025-01-01", -10_000), ("2026-01-01", 11_000)]) == pytest.approx(
        0.10, abs=1e-4
    )
    assert xirr([("2025-01-01", 10_000), ("2026-01-01", 11_000)]) is None
    assert xirr([("2026-01-01", -10_000), ("2026-01-10", 11_000)]) is None


def test_xirr_loss_case_and_multi_flow_npv_sign_checks():
    loss = xirr([("2025-01-01", -10_000), ("2026-01-01", 9_000)])
    assert loss == pytest.approx(-0.10, abs=1e-4)

    flows = [("2025-01-01", -10_000), ("2025-07-01", -1_000), ("2026-01-01", 13_000)]
    rate = xirr(flows)
    assert rate is not None

    first = datetime.fromisoformat(flows[0][0]).date()

    def npv(r: float) -> float:
        return sum(
            amount / ((1 + r) ** ((datetime.fromisoformat(day).date() - first).days / 365.25))
            for day, amount in flows
        )

    assert isclose(npv(rate), 0, abs_tol=1e-3)
    assert npv(rate - 0.001) > 0
    assert npv(rate + 0.001) < 0


def test_compute_returns_excludes_internal_trades_and_rolls_realized_by_year():
    apply_transaction(_txn("2025-01-01", "deposit", amount=10_000))
    apply_transaction(_txn("2025-01-02", "buy", symbol="AAPL", shares=50, price=100))
    apply_transaction(_txn("2026-01-03", "sell", symbol="AAPL", shares=10, price=120))
    apply_transaction(_txn("2026-01-04", "withdraw", amount=500))

    summary = compute_returns(_valuation(total_with_cash=11_000))

    assert summary.total_deposited == 10_000
    assert summary.total_withdrawn == 500
    assert summary.realized_pl_total == 200
    assert summary.realized_pl_by_year == {"2026": 200}
    assert summary.first_flow_date == "2025-01-01"
    assert summary.flow_count == 2
    assert summary.mwr_annualized is not None


def test_compute_returns_unpriced_portfolio_has_no_mwr():
    apply_transaction(_txn("2025-01-01", "deposit", amount=10_000))

    summary = compute_returns(_valuation(total_with_cash=10_000, unpriced_symbols=["AAPL"]))

    assert summary.mwr_annualized is None
    assert summary.mwr_note == "portfolio not fully priced"


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def fetch_ticker_data(symbol: str) -> TickerData:
        return TickerData(
            symbol=symbol,
            fetched_at="2026-07-05T00:00:00Z",
            quote=Quote(price=10, currency="USD", change=0, change_percent=0),
        )

    monkeypatch.setattr(holdings_mod, "fetch_ticker_data", fetch_ticker_data)
    return TestClient(web_app.app)


def test_transaction_route_valid_buy_updates_holdings(monkeypatch):
    client = _client(monkeypatch)
    db.set_cash(1_000)

    response = client.post(
        "/portfolio/transactions",
        data={
            "ts": "2026-01-02",
            "side": "buy",
            "symbol": "aapl",
            "shares": "3",
            "price": "10",
            "amount": "999999",
            "note": "ignored amount",
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "txns-changed, holdings-changed"
    assert list_holdings() == [Holding(symbol="AAPL", shares=3, avg_cost=10)]
    assert "ignored amount" in response.text
    assert "$30" in response.text

    holdings = client.get("/portfolio/holdings")
    assert "AAPL" in holdings.text
    assert "3" in holdings.text


def test_transaction_route_invalid_returns_error_partial(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/portfolio/transactions",
        data={
            "ts": "2026-01-02",
            "side": "sell",
            "symbol": "AAPL",
            "shares": "1",
            "price": "10",
            "amount": "",
        },
    )

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "sell shares exceed held shares" in response.text


def test_transaction_csv_import_happy_path_and_bad_rows(monkeypatch):
    client = _client(monkeypatch)
    csv = (
        "date,side,symbol,shares,price,amount,note\n"
        "2026-01-01,deposit,,,,1000,seed\n"
        "2026-01-02,buy,AAPL,2,10,999,buy ignores amount\n"
        "2026-01-03,buy,MSFT,1,,10,bad\n"
    )

    response = client.post(
        "/portfolio/transactions/import",
        files={"file": ("txns.csv", csv.encode(), "text/csv")},
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "txns-changed, holdings-changed"
    assert "Imported 2 transactions" in response.text
    assert "line 4: price must be &gt; 0" in response.text
    assert list_holdings() == [Holding(symbol="AAPL", shares=2, avg_cost=10)]
    assert db.get_cash() == 980


def test_transaction_delete_removes_row_not_effect(monkeypatch):
    client = _client(monkeypatch)
    saved = apply_transaction(_txn("2026-01-01", "deposit", amount=500))

    response = client.post(f"/portfolio/transactions/delete/{saved.id}")

    assert response.status_code == 200
    assert list_transactions() == []
    assert db.get_cash() == 500
    assert "No transactions yet" in response.text
