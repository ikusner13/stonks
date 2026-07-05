import pytest

from app import config
from app.portfolio import holdings as holdings_mod
from app.portfolio.holdings import (
    Holding,
    list_holdings,
    remove_holding,
    upsert_holding,
    value_holdings,
)
from app.schemas import Quote, TickerData


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    holdings_mod.init_holdings_db()


def _ticker(symbol: str, price: float | None) -> TickerData:
    return TickerData(
        symbol=symbol,
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=price, currency="USD", change=0, change_percent=0)
        if price is not None
        else None,
    )


def test_upsert_holding_overwrites_existing_row():
    upsert_holding("aapl", 2, 100)
    upsert_holding("AAPL", 3, 125)

    assert list_holdings() == [Holding(symbol="AAPL", shares=3, avg_cost=125)]


def test_remove_holding_is_idempotent():
    upsert_holding("MSFT", 4, 50)

    remove_holding("msft")
    remove_holding("msft")

    assert list_holdings() == []


@pytest.mark.parametrize("missing_raises", [False, True])
async def test_value_holdings_aggregates_only_consistently_priced_set(
    monkeypatch: pytest.MonkeyPatch, missing_raises: bool
):
    monkeypatch.setattr(
        holdings_mod,
        "list_holdings",
        lambda: [
            Holding(symbol="AAA", shares=10, avg_cost=5),
            Holding(symbol="BBB", shares=5, avg_cost=20),
            Holding(symbol="MISS", shares=4, avg_cost=100),
        ],
    )

    async def fetch_ticker_data(symbol: str) -> TickerData:
        if symbol == "AAA":
            return _ticker(symbol, 10)
        if symbol == "BBB":
            return _ticker(symbol, 30)
        if missing_raises:
            raise RuntimeError("price unavailable")
        return _ticker(symbol, None)

    monkeypatch.setattr(holdings_mod, "fetch_ticker_data", fetch_ticker_data)

    valuation = await value_holdings()

    assert valuation.total_value == 250
    assert valuation.total_cost == 150
    assert valuation.total_unrealized_pl == 100
    assert valuation.total_unrealized_pl_pct == pytest.approx(100 / 150)
    assert valuation.unpriced_symbols == ["MISS"]
    weights = [h.weight for h in valuation.holdings if h.weight is not None]
    assert sum(weights) == pytest.approx(1.0)


async def test_value_holdings_empty_portfolio(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(holdings_mod, "list_holdings", lambda: [])

    async def fetch_ticker_data(symbol: str) -> TickerData:
        raise AssertionError(f"unexpected price fetch for {symbol}")

    monkeypatch.setattr(holdings_mod, "fetch_ticker_data", fetch_ticker_data)

    valuation = await value_holdings()

    assert valuation.holdings == []
    assert valuation.total_value == 0
    assert valuation.total_cost == 0
    assert valuation.total_unrealized_pl == 0
    assert valuation.total_unrealized_pl_pct == 0
    assert valuation.unpriced_symbols == []
