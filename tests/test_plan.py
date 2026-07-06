import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.portfolio.decision_support import analyze_drift
from app.portfolio.holdings import HoldingValuation, PortfolioValuation
from app.portfolio.optimize import OptimizeResult, PortfolioMetrics
from app.portfolio.plan import (
    ContributionPlan,
    Target,
    init_targets_db,
    list_targets,
    plan_contribution,
    plan_rebalance,
    set_targets,
)
from app.web import api


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
    assert plan.cash_after == 125
    assert plan.cash_target_weight == pytest.approx(0.45)
    assert plan.untargeted == ["UNT", "UNP"]

    by_symbol = {item.symbol: item for item in plan.items}
    assert by_symbol["AAA"].current_weight == pytest.approx(0.20)
    assert by_symbol["AAA"].drift == pytest.approx(-0.10)
    assert by_symbol["AAA"].action == "buy"
    assert by_symbol["AAA"].delta_usd == 50
    assert by_symbol["AAA"].delta_shares == 5
    assert by_symbol["AAA"].after_weight == pytest.approx(0.30)

    assert by_symbol["BBB"].drift == pytest.approx(0.05)
    assert by_symbol["BBB"].action == "sell"
    assert by_symbol["BBB"].delta_usd == -25
    assert by_symbol["BBB"].after_weight == pytest.approx(0.15)

    assert by_symbol["ZERO"].target_weight == 0
    assert by_symbol["ZERO"].action == "sell"
    assert by_symbol["ZERO"].delta_usd == -100
    assert by_symbol["ZERO"].delta_shares == -5
    assert by_symbol["ZERO"].after_weight == pytest.approx(0)

    assert by_symbol["NEW"].price is None
    assert by_symbol["NEW"].action == "buy"
    assert by_symbol["NEW"].delta_usd == 50
    assert by_symbol["NEW"].delta_shares is None
    assert by_symbol["NEW"].after_weight == pytest.approx(0.10)

    drifts = [abs(item.drift) for item in plan.items]
    assert drifts == sorted(drifts, reverse=True)
    assert plan.items[0].symbol == "ZERO"
    assert plan.items[-1].symbol == "BBB"


def test_plan_rebalance_uses_dual_drift_bands():
    valuation = PortfolioValuation(
        holdings=[
            _holding("SMALL", market_value=520),
            _holding("CORE", market_value=4_400),
            _holding("ABS", market_value=4_600),
        ],
        total_value=9_520,
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof="2026-07-05T00:00:00+00:00",
        cash=480,
        total_with_cash=10_000,
        cash_pct=0.048,
    )
    targets = [
        Target(symbol="SMALL", target_weight=0.04),
        Target(symbol="CORE", target_weight=0.40),
        Target(symbol="ABS", target_weight=0.40),
    ]

    plan = plan_rebalance(valuation, targets)

    assert plan is not None
    assert plan.threshold == pytest.approx(0.05)
    assert plan.relative_threshold == pytest.approx(0.20)
    by_symbol = {item.symbol: item for item in plan.items}
    assert by_symbol["SMALL"].drift == pytest.approx(0.012)
    assert by_symbol["SMALL"].action == "sell"
    assert by_symbol["CORE"].drift == pytest.approx(0.04)
    assert by_symbol["CORE"].action == "hold"
    assert by_symbol["ABS"].drift == pytest.approx(0.06)
    assert by_symbol["ABS"].action == "sell"


def test_plan_rebalance_holds_exact_absolute_boundary_when_relative_band_does_not_trip():
    valuation = PortfolioValuation(
        holdings=[_holding("CORE", market_value=4_500)],
        total_value=4_500,
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof="2026-07-05T00:00:00+00:00",
        cash=5_500,
        total_with_cash=10_000,
        cash_pct=0.55,
    )

    plan = plan_rebalance(valuation, [Target(symbol="CORE", target_weight=0.40)])

    assert plan is not None
    item = plan.items[0]
    assert item.drift == pytest.approx(0.05)
    assert item.action == "hold"


def test_analyze_drift_uses_relative_band_and_zero_target_only_gets_absolute_band():
    result = OptimizeResult(
        asof="2026-07-05T00:00:00+00:00",
        objective="max_sharpe",
        lookback_days=730,
        symbols=["SMALL", "CORE", "ABS", "ZERO"],
        optimal=PortfolioMetrics(
            weights={"SMALL": 0.04, "CORE": 0.40, "ABS": 0.40},
            expected_return=0,
            volatility=0,
            sharpe=0,
        ),
        current=PortfolioMetrics(
            weights={"SMALL": 0.052, "CORE": 0.44, "ABS": 0.46, "ZERO": 0.04},
            expected_return=0,
            volatility=0,
            sharpe=0,
        ),
    )

    drift = analyze_drift(result)

    assert drift is not None
    assert drift.threshold_pct == pytest.approx(0.05)
    assert drift.relative_threshold_pct == pytest.approx(0.20)
    by_symbol = {item.symbol: item for item in drift.items}
    assert by_symbol["SMALL"].significant is True
    assert "Overweight by 1.2 pts" in by_symbol["SMALL"].suggestion
    assert "30% of its 4% target" in by_symbol["SMALL"].suggestion
    assert by_symbol["CORE"].significant is False
    assert by_symbol["ABS"].significant is True
    assert by_symbol["ZERO"].target_weight == 0
    assert by_symbol["ZERO"].significant is False


def test_plan_rebalance_returns_none_without_base_or_targets():
    assert plan_rebalance(_valuation(), []) is None
    empty_base = _valuation().model_copy(update={"total_with_cash": 0})
    assert plan_rebalance(empty_base, [Target(symbol="AAA", target_weight=0.5)]) is None


def _by_symbol(plan: ContributionPlan) -> dict[str, object]:
    return {item.symbol: item for item in plan.items}


def test_plan_contribution_proportional_split_when_short():
    targets = [
        Target(symbol="AAA", target_weight=0.40),
        Target(symbol="BBB", target_weight=0.40),
        Target(symbol="ZERO", target_weight=0.20),
    ]

    plan = plan_contribution(_valuation(), targets, 100)

    assert plan is not None
    assert plan.contribution == 100
    assert plan.base_after == 600
    assert plan.leftover_cash == 0
    by_symbol = _by_symbol(plan)
    assert by_symbol["AAA"].buy_usd == pytest.approx(46.66)
    assert by_symbol["BBB"].buy_usd == pytest.approx(46.67)
    assert by_symbol["ZERO"].buy_usd == pytest.approx(6.67)
    assert by_symbol["ZERO"].buy_shares == pytest.approx(0.3335)


def test_plan_contribution_full_deficit_leftover_and_after_weights_sum_to_targets():
    targets = [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.25),
        Target(symbol="ZERO", target_weight=0.20),
    ]

    plan = plan_contribution(_valuation(), targets, 400)

    assert plan is not None
    assert plan.base_after == 900
    by_symbol = _by_symbol(plan)
    assert by_symbol["AAA"].buy_usd == 170
    assert by_symbol["BBB"].buy_usd == 125
    assert by_symbol["ZERO"].buy_usd == 80
    assert plan.leftover_cash == 25
    assert by_symbol["AAA"].after_weight == pytest.approx(0.30)
    assert by_symbol["BBB"].after_weight == pytest.approx(0.25)
    assert by_symbol["ZERO"].after_weight == pytest.approx(0.20)


def test_plan_contribution_overweight_symbol_gets_no_buy_and_unpriced_shares_none():
    targets = [
        Target(symbol="AAA", target_weight=0.10),
        Target(symbol="UNP", target_weight=0.30),
    ]

    plan = plan_contribution(_valuation(), targets, 100)

    assert plan is not None
    by_symbol = _by_symbol(plan)
    assert "AAA" not in by_symbol
    assert by_symbol["UNP"].buy_usd == 100
    assert by_symbol["UNP"].buy_shares is None
    assert by_symbol["UNP"].current_weight == 0
    assert by_symbol["UNP"].after_weight == pytest.approx(100 / 600)


def test_plan_contribution_returns_none_for_non_positive_or_invalid_base():
    targets = [Target(symbol="AAA", target_weight=0.30)]

    assert plan_contribution(_valuation(), targets, 0) is None
    assert plan_contribution(_valuation(), targets, -1) is None
    assert plan_contribution(_valuation(), [], 100) is None
    empty_base = _valuation().model_copy(update={"total_with_cash": 0})
    assert plan_contribution(empty_base, targets, 100) is None


def test_targets_routes_valid_and_invalid(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(api, "value_holdings", value_holdings)
    client = TestClient(web_app.app)

    response = client.put(
        "/api/portfolio/targets",
        json={
            "targets": [
                {"symbol": "aaa", "weight_pct": 30},
                {"symbol": "BBB", "weight_pct": 15},
            ],
        },
    )

    assert response.status_code == 200
    assert list_targets() == [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
    ]
    payload = response.json()
    assert payload["implicit_cash_weight"] == pytest.approx(0.55)
    assert payload["rows"][0] == {"symbol": "AAA", "weight_pct": 30.0}
    assert payload["rows"][1] == {"symbol": "BBB", "weight_pct": 15.0}

    response = client.put(
        "/api/portfolio/targets",
        json={
            "targets": [
                {"symbol": "AAA", "weight_pct": 70},
                {"symbol": "BBB", "weight_pct": 50},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "target weights sum to 120%"
    assert list_targets() == [
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
    ]


def test_rebalance_route_with_and_without_targets(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(api, "value_holdings", value_holdings)
    client = TestClient(web_app.app)

    response = client.get("/api/portfolio/rebalance")

    assert response.status_code == 200
    assert response.json()["has_targets"] is False
    assert response.json()["plan"] is None

    set_targets([
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.15),
        Target(symbol="ZERO", target_weight=0),
    ])
    response = client.get("/api/portfolio/rebalance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_targets"] is True
    assert {item["symbol"] for item in payload["plan"]["items"]} == {"AAA", "BBB", "ZERO"}
    assert payload["plan"]["untargeted"] == ["UNT", "UNP"]


def test_whatif_route_valid_and_invalid_amount(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(api, "value_holdings", value_holdings)
    set_targets([
        Target(symbol="AAA", target_weight=0.30),
        Target(symbol="BBB", target_weight=0.25),
        Target(symbol="ZERO", target_weight=0.20),
    ])
    client = TestClient(web_app.app)

    response = client.post("/api/portfolio/whatif", json={"amount": 100})

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_targets"] is True
    assert {item["symbol"] for item in payload["plan"]["items"]} >= {"AAA", "BBB"}
    assert payload["plan"]["leftover_cash"] >= 0
    assert payload["disclaimer"]

    response = client.post("/api/portfolio/whatif", json={"amount": 0})

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "Contribution amount must be a positive number."
