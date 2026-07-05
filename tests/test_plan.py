import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.portfolio.holdings import HoldingValuation, PortfolioValuation
from app.portfolio.plan import (
    Target,
    init_targets_db,
    list_targets,
    plan_rebalance,
    set_targets,
)


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    init_targets_db()


def _holding(
    symbol: str,
    *,
    shares: float = 10,
    price: float | None = 10,
    market_value: float | None = 100,
) -> HoldingValuation:
    return HoldingValuation(
        symbol=symbol,
        shares=shares,
        avg_cost=5,
        price=price,
        market_value=market_value,
        cost_value=shares * 5,
        unrealized_pl=None,
        unrealized_pl_pct=None,
        weight=None,
    )


def _valuation() -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[
            _holding("AAA"),
            _holding("BBB"),
            _holding("ZERO", shares=5, price=20, market_value=100),
            _holding("UNT"),
            _holding("UNP", shares=1, price=None, market_value=None),
        ],
        total_value=400,
        total_cost=200,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof="2026-07-05T00:00:00+00:00",
        unpriced_symbols=["UNP"],
        cash=100,
        total_with_cash=500,
        cash_pct=0.2,
    )


def test_set_targets_validates_and_replaces():
    set_targets([Target(symbol="aapl", target_weight=0.6)])
    assert list_targets() == [Target(symbol="AAPL", target_weight=0.6)]

    with pytest.raises(ValueError, match="between 0% and 100%"):
        set_targets([Target(symbol="AAPL", target_weight=-0.01)])

    with pytest.raises(ValueError, match="sum to 112%"):
        set_targets([
            Target(symbol="AAPL", target_weight=0.6),
            Target(symbol="MSFT", target_weight=0.52),
        ])

    assert list_targets() == [Target(symbol="AAPL", target_weight=0.6)]

    set_targets([Target(symbol="MSFT", target_weight=0.25)])
    assert list_targets() == [Target(symbol="MSFT", target_weight=0.25)]


def test_plan_rebalance_math_and_edge_cases():
    targets = [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
        Target(symbol="ZERO", target_weight=0),
        Target(symbol="NEW", target_weight=0.10),
    ]

    plan = plan_rebalance(_valuation(), targets)

    assert plan is not None
    assert plan.base_value == 500
    assert plan.cash_now == 100
    assert plan.cash_after == 100
    assert plan.cash_target_weight == pytest.approx(0.45)
    assert plan.untargeted == ["UNT", "UNP"]

    by_symbol = {item.symbol: item for item in plan.items}
    assert by_symbol["AAA"].current_weight == pytest.approx(0.20)
    assert by_symbol["AAA"].drift == pytest.approx(-0.10)
    assert by_symbol["AAA"].action == "buy"
    assert by_symbol["AAA"].delta_usd == 50
    assert by_symbol["AAA"].delta_shares == 5

    assert by_symbol["BBB"].drift == pytest.approx(0.05)
    assert by_symbol["BBB"].action == "hold"
    assert by_symbol["BBB"].delta_usd == 0

    assert by_symbol["ZERO"].target_weight == 0
    assert by_symbol["ZERO"].action == "sell"
    assert by_symbol["ZERO"].delta_usd == -100
    assert by_symbol["ZERO"].delta_shares == -5

    assert by_symbol["NEW"].price is None
    assert by_symbol["NEW"].action == "buy"
    assert by_symbol["NEW"].delta_usd == 50
    assert by_symbol["NEW"].delta_shares is None

    drifts = [abs(item.drift) for item in plan.items]
    assert drifts == sorted(drifts, reverse=True)
    assert plan.items[0].symbol == "ZERO"
    assert plan.items[-1].symbol == "BBB"


def test_plan_rebalance_returns_none_without_base_or_targets():
    assert plan_rebalance(_valuation(), []) is None
    empty_base = _valuation().model_copy(update={"total_with_cash": 0})
    assert plan_rebalance(empty_base, [Target(symbol="AAA", target_weight=0.5)]) is None


def test_targets_routes_valid_and_invalid(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(web_app, "value_holdings", value_holdings)
    client = TestClient(web_app.app)

    response = client.post(
        "/portfolio/targets",
        data={
            "symbol[]": ["aaa", "BBB", "UNT"],
            "weight_pct[]": ["30", "15", ""],
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "targets-changed"
    assert list_targets() == [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
    ]
    assert "Implicit cash target" in response.text
    assert "55.0%" in response.text

    response = client.post(
        "/portfolio/targets",
        data={
            "symbol[]": ["AAA", "BBB"],
            "weight_pct[]": ["70", "50"],
        },
    )

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "target weights sum to 120%" in response.text
    assert list_targets() == [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
    ]


def test_rebalance_route_with_and_without_targets(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(web_app, "value_holdings", value_holdings)
    client = TestClient(web_app.app)

    response = client.get("/portfolio/rebalance")

    assert response.status_code == 200
    assert "Set target allocations" in response.text

    set_targets([
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
        Target(symbol="ZERO", target_weight=0),
    ])
    response = client.get("/portfolio/rebalance")

    assert response.status_code == 200
    assert "Rebalance plan" not in response.text
    assert "AAA" in response.text
    assert "ZERO" in response.text
    assert "Untargeted holdings excluded from trades" in response.text
