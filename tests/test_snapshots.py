import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.db import connect
from app.portfolio.holdings import PortfolioValuation
from app.portfolio.snapshots import (
    NavSnapshot,
    build_nav_series,
    init_snapshots_db,
    list_snapshots,
    record_snapshot,
)


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    init_snapshots_db()


def _valuation(
    *,
    total_value: float = 100.0,
    cash: float = 0.0,
    total_cost: float = 80.0,
    unrealized_pl: float = 20.0,
    unpriced_symbols: list[str] | None = None,
) -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[],
        total_value=total_value,
        total_cost=total_cost,
        total_unrealized_pl=unrealized_pl,
        total_unrealized_pl_pct=(unrealized_pl / total_cost) if total_cost else 0.0,
        asof="2026-07-05T00:00:00+00:00",
        unpriced_symbols=unpriced_symbols or [],
        cash=cash,
        total_with_cash=total_value + cash,
        cash_pct=(cash / (total_value + cash)) if (total_value + cash) > 0 else 0.0,
    )


def test_record_snapshot_replaces_same_day():
    assert record_snapshot(_valuation(total_value=100, cash=25))
    assert record_snapshot(_valuation(total_value=150, cash=50, total_cost=90, unrealized_pl=60))

    snapshots = list_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].total_value == 150
    assert snapshots[0].cash == 50
    assert snapshots[0].total_with_cash == 200
    assert snapshots[0].total_cost == 90
    assert snapshots[0].unrealized_pl == 60


def test_record_snapshot_skips_unpriced_symbols_and_zero_value():
    assert not record_snapshot(_valuation(unpriced_symbols=["MISS"]))
    assert not record_snapshot(_valuation(total_value=0, cash=0, total_cost=0, unrealized_pl=0))

    assert list_snapshots() == []


def test_build_nav_series_computes_deltas_without_web_chart():
    series = build_nav_series([
        NavSnapshot(
            day="2026-07-01",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        ),
        NavSnapshot(
            day="2026-07-02",
            total_value=120,
            cash=0,
            total_with_cash=120,
            total_cost=90,
            unrealized_pl=30,
        ),
        NavSnapshot(
            day="2026-07-03",
            total_value=110,
            cash=20,
            total_with_cash=130,
            total_cost=90,
            unrealized_pl=20,
        ),
    ])

    assert series.change_1d == 10
    assert series.change_1d_pct == pytest.approx(10 / 120)
    assert series.change_total == 30
    assert series.change_total_pct == pytest.approx(30 / 100)
    assert not hasattr(series, "chart")


def test_build_nav_series_handles_single_and_flat_series():
    one_point = build_nav_series([
        NavSnapshot(
            day="2026-07-01",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        )
    ])
    assert one_point.change_1d is None
    assert one_point.change_total is None

    flat = build_nav_series([
        NavSnapshot(
            day="2026-07-01",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        ),
        NavSnapshot(
            day="2026-07-02",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        ),
    ])
    assert flat.change_1d == 0
    assert not hasattr(flat, "chart")


def test_nav_route_renders_partial():
    with connect() as c:
        c.execute(
            """
            INSERT INTO nav_snapshots (
                day, total_value, cash, total_with_cash, total_cost, unrealized_pl
            )
            VALUES
                ('2026-07-01', 100, 0, 100, 80, 20),
                ('2026-07-02', 125, 25, 150, 80, 45)
            """
        )

    from app.web import app as web_app

    client = TestClient(web_app.app)
    response = client.get("/portfolio/nav")

    assert response.status_code == 200
    assert "Portfolio NAV history" in response.text
    assert "$150" in response.text
    assert "2026-07-02" in response.text


def test_portfolio_page_ignores_snapshot_failure(monkeypatch: pytest.MonkeyPatch):
    from app.web import app as web_app

    async def value_holdings() -> PortfolioValuation:
        return _valuation(total_value=0, cash=0, total_cost=0, unrealized_pl=0)

    def record_snapshot(_valuation: PortfolioValuation) -> bool:
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(web_app, "value_holdings", value_holdings)
    monkeypatch.setattr(web_app, "record_snapshot", record_snapshot)
    client = TestClient(web_app.app)

    response = client.get("/portfolio")

    assert response.status_code == 200
    assert "Portfolio" in response.text
