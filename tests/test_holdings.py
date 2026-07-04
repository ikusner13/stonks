import pytest

from app.portfolio import holdings as holdings_mod
from app.portfolio.holdings import Holding, value_holdings
from app.schemas import Quote, TickerData


def _ticker(symbol: str, price: float | None) -> TickerData:
    return TickerData(
        symbol=symbol,
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=price, currency="USD", change=0, change_percent=0)
        if price is not None
        else None,
    )


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
