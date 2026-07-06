from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.broker.reconcile import HoldingsDiff
from app.broker.sync import SyncResult
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.llm.budget import BudgetExceededError
from app.llm.pipeline import InsufficientDataError
from app.portfolio import holdings as holdings_mod
from app.portfolio.decision_support import CorrelationInsight, PositionSizeGuidance, RegimeSignal
from app.portfolio.holdings import (
    Holding,
    HoldingValuation,
    PortfolioValuation,
    init_holdings_db,
    list_holdings,
)
from app.portfolio.optimize import FrontierPoint, NoDataError, OptimizeResult, PortfolioMetrics
from app.portfolio.performance import PerformanceMetrics
from app.portfolio.plan import Target, init_targets_db, list_targets
from app.portfolio.snapshots import init_snapshots_db, list_snapshots
from app.portfolio.transactions import init_transactions_db, list_transactions
from app.portfolio.twr import TWRSummary
from app.schemas import (
    Candidate,
    Critique,
    DiscoveryResult,
    FabricationCheck,
    Quote,
    ResearchResult,
    Thesis,
    TickerData,
    TickerReport,
)
from app.web import api
from app.web import app as web_app


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    init_holdings_db()
    init_targets_db()
    init_transactions_db()
    init_snapshots_db()

    async def fetch_ticker_data(symbol: str) -> TickerData:
        prices = {"AAA": 10.0, "BBB": 20.0, "PENY": 4.0, "AAPL": 10.0, "MSFT": 20.0}
        return TickerData(
            symbol=symbol,
            fetched_at="2026-07-05T00:00:00Z",
            quote=Quote(
                price=prices.get(symbol.upper(), 10.0),
                currency="USD",
                change=0,
                change_percent=0,
            ),
        )

    monkeypatch.setattr(holdings_mod, "fetch_ticker_data", fetch_ticker_data)


@pytest.fixture
def client() -> TestClient:
    return TestClient(web_app.app)


def _research_result(*, profile: str = "largecap") -> ResearchResult:
    return ResearchResult(
        ticker=TickerData(symbol="PENY", fetched_at="2026-07-05T00:00:00Z"),
        report=TickerReport(
            symbol="PENY",
            company_name="Penny Corp",
            summary="Sparse but sufficient.",
            thesis=Thesis(bull=["Bull"], bear=["Bear"]),
            key_metrics=[],
            valuation_context="Context.",
            risks=[],
            things_to_investigate=[],
            confidence="medium",
        ),
        critique=Critique(
            fabrication_check=FabricationCheck(passed=True, details="ok"),
            issues=[],
            suggested_confidence="medium",
            overall_assessment="ok",
        ),
        revised=False,
        scorecard=IndicatorScorecard(
            symbol="PENY",
            asof="2026-07-05T00:00:00Z",
            profile=profile,
            indicators=[
                Indicator(
                    key="avg_dollar_volume_20d",
                    label="Avg daily dollar volume (20d)",
                    value=530_000.0,
                    unit="usd",
                    signal="neutral",
                    detail="test",
                )
            ],
            bullish=0,
            bearish=0,
            neutral=1,
            unavailable=0,
            data_completeness=1.0,
        ),
        confidence_assessment=None,
        profile=profile,
        profile_reason="manual override",
    )


def _valuation() -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[
            HoldingValuation(
                symbol="AAA",
                shares=10,
                avg_cost=5,
                price=10,
                market_value=100,
                cost_value=50,
                unrealized_pl=50,
                unrealized_pl_pct=1,
                weight=0.4,
            ),
            HoldingValuation(
                symbol="BBB",
                shares=5,
                avg_cost=10,
                price=20,
                market_value=100,
                cost_value=50,
                unrealized_pl=50,
                unrealized_pl_pct=1,
                weight=0.4,
            ),
        ],
        total_value=200,
        total_cost=100,
        total_unrealized_pl=100,
        total_unrealized_pl_pct=1,
        cash=50,
        total_with_cash=250,
        cash_pct=0.2,
        asof="2026-07-05T00:00:00Z",
    )


def _optimizer_result(symbols: list[str]) -> OptimizeResult:
    metrics = PortfolioMetrics(
        weights={symbol: round(1 / len(symbols), 6) for symbol in symbols},
        expected_return=0.1,
        volatility=0.2,
        sharpe=0.5,
    )
    return OptimizeResult(
        asof="2026-07-05T00:00:00Z",
        objective="max_sharpe",
        lookback_days=730,
        symbols=symbols,
        optimal=metrics,
        current=metrics,
        efficient_frontier=[
            FrontierPoint(expected_return=0.05, volatility=0.1, sharpe=0.5),
        ],
        warnings=[],
    )


def test_meta_watchlist_holdings_cash_and_portfolio(client: TestClient):
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert isinstance(meta.json()["examples"], list)

    added = client.put("/api/watchlist/aapl")
    assert added.status_code == 200
    assert added.json() == {"symbol": "AAPL", "watched": True}
    assert client.get("/api/watchlist").json()["items"][0]["symbol"] == "AAPL"
    removed = client.delete("/api/watchlist/aapl")
    assert removed.status_code == 200
    assert removed.json() == {"symbol": "AAPL", "watched": False}

    response = client.put(
        "/api/portfolio/holdings",
        json={"symbol": "aaa", "shares": 2, "avg_cost": 5},
    )
    assert response.status_code == 200
    assert response.json()["valuation"]["holdings"][0]["symbol"] == "AAA"
    assert list_holdings() == [Holding(symbol="AAA", shares=2, avg_cost=5)]
    holdings_get = client.get("/api/portfolio/holdings")
    assert holdings_get.status_code == 200
    assert holdings_get.json()["valuation"]["holdings"][0]["symbol"] == "AAA"

    response = client.put("/api/portfolio/cash", json={"cash": 25})
    assert response.status_code == 200
    assert response.json()["valuation"]["cash"] == 25

    portfolio = client.get("/api/portfolio")
    assert portfolio.status_code == 200
    payload = portfolio.json()
    assert payload["optimizer_seed"][0]["symbol"] == "AAA"
    assert payload["allocation"]
    assert payload["disclaimer"]
    assert len(list_snapshots()) == 1

    deleted = client.delete("/api/portfolio/holdings/aaa")
    assert deleted.status_code == 200
    assert deleted.json()["valuation"]["holdings"] == []


def test_discover_and_research_happy_paths(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    async def fake_discover_ideas(goal: str) -> DiscoveryResult:
        return DiscoveryResult(
            goal=goal,
            interpretation="small profitable names",
            candidates=[
                Candidate(
                    symbol="AAA",
                    name="AAA Inc",
                    market_cap=1_000_000,
                    pe_ratio=10,
                    rationale="fits",
                    source="theme",
                )
            ],
        )

    async def fake_research_ticker_cached(symbol, mode, *, profile_override=None, fresh=False):
        assert (symbol, mode, profile_override, fresh) == ("PENY", "cheap", "penny", True)
        return _research_result(profile="penny")

    def fake_suggest_position_size(
        portfolio_value,
        confidence,
        symbol=None,
        *,
        current_weight=None,
        profile,
        adv_dollars=None,
    ):
        assert adv_dollars == 530_000
        return PositionSizeGuidance(
            symbol=symbol,
            confidence=confidence,
            portfolio_value=portfolio_value,
            low_pct=0.01,
            high_pct=0.03,
            low_dollars=1,
            high_dollars=3,
            note="ok",
            profile=profile.key,
        )

    async def fake_value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(api, "discover_ideas", fake_discover_ideas)
    monkeypatch.setattr(api, "research_ticker_cached", fake_research_ticker_cached)
    monkeypatch.setattr(api, "value_holdings", fake_value_holdings)
    monkeypatch.setattr(api, "suggest_position_size", fake_suggest_position_size)

    response = client.post("/api/discover", json={"goal": "small caps"})
    assert response.status_code == 200
    assert response.json()["result"]["candidates"][0]["symbol"] == "AAA"
    assert isinstance(response.json()["watched_symbols"], list)

    response = client.get("/api/research/peny?mode=cheap&fresh=1&profile=penny")
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["report"]["symbol"] == "PENY"
    assert payload["profile_label"] == "Penny / micro-cap"
    assert payload["effective_confidence"] == "medium"


def test_research_error_envelopes(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    async def budget_fail(*args, **kwargs):
        raise BudgetExceededError(5.0, 5.0)

    monkeypatch.setattr(api, "research_ticker_cached", budget_fail)
    response = client.get("/api/research/AAPL")
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "budget_exceeded"

    async def insufficient_fail(*args, **kwargs):
        raise InsufficientDataError("AAPL", {"quote": "empty"})

    monkeypatch.setattr(api, "research_ticker_cached", insufficient_fail)
    response = client.get("/api/research/aapl")
    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "insufficient_data",
        "message": "No market data found for AAPL — check the ticker symbol.",
    }


def test_invalid_inputs_return_400(client: TestClient):
    assert client.post("/api/discover", json={"goal": "   "}).status_code == 400
    response = client.put("/api/portfolio/holdings", json={"symbol": " ", "shares": 1})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_input"

    response = client.put("/api/portfolio/cash", json={"cash": "nan"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_input"


def test_csv_imports_valid_and_fatal_errors(client: TestClient):
    response = client.post(
        "/api/portfolio/holdings/import",
        files={"file": ("holdings.csv", b"symbol,shares,avg_cost\naapl,2,5\nbad,nope,1\n")},
    )
    assert response.status_code == 200
    assert response.json()["import_summary"] == {
        "imported": 1,
        "skipped": ["line 3: bad shares 'nope'"],
    }

    response = client.post(
        "/api/portfolio/transactions/import",
        files={
            "file": (
                "txns.csv",
                b"date,side,symbol,shares,price,amount,note\n"
                b"2026-01-01,deposit,,,,1000,seed\n",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["import_summary"]["imported"] == 1

    response = client.post(
        "/api/portfolio/holdings/import",
        files={"file": ("big.csv", b"symbol,shares\n" + b"a,1\n" * 30_000)},
    )
    assert response.status_code == 400
    assert "100 KB" in response.json()["detail"]["message"]

    response = client.post(
        "/api/portfolio/transactions/import",
        files={"file": ("bad.csv", b"symbol,amount\nAAA,1\n")},
    )
    assert response.status_code == 400
    assert "date and side" in response.json()["detail"]["message"]


def test_transactions_create_delete_and_validation(client: TestClient):
    deposit = client.post(
        "/api/portfolio/transactions",
        json={"ts": "2026-01-01", "side": "deposit", "amount": 1000},
    )
    assert deposit.status_code == 200

    buy = client.post(
        "/api/portfolio/transactions",
        json={
            "ts": "2026-01-02",
            "side": "BUY",
            "symbol": "aaa",
            "shares": 2,
            "price": 10,
            "amount": 999999,
        },
    )
    assert buy.status_code == 200
    txn = buy.json()["transactions"][0]
    assert txn["side"] == "buy"
    assert txn["amount"] == 20
    assert list_transactions()[0].amount == 20
    txns_get = client.get("/api/portfolio/transactions")
    assert txns_get.status_code == 200
    assert txns_get.json()["transactions"][0]["amount"] == 20

    invalid = client.post(
        "/api/portfolio/transactions",
        json={"ts": "2026-01-03", "side": "dividend", "symbol": "AAA"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_input"

    deleted = client.delete(f"/api/portfolio/transactions/{txn['id']}")
    assert deleted.status_code == 200
    assert all(t["id"] != txn["id"] for t in deleted.json()["transactions"])


def test_targets_rebalance_whatif_and_pct_conversion(client: TestClient):
    response = client.put(
        "/api/portfolio/holdings",
        json={"symbol": "aaa", "shares": 10, "avg_cost": 5},
    )
    assert response.status_code == 200

    response = client.put(
        "/api/portfolio/targets",
        json={"targets": [{"symbol": "aaa", "weight_pct": 40}, {"symbol": "", "weight_pct": 1}]},
    )
    assert response.status_code == 200
    assert list_targets() == [Target(symbol="AAA", target_weight=0.40)]

    get_response = client.get("/api/portfolio/targets")
    assert get_response.status_code == 200
    assert get_response.json()["rows"][0]["weight_pct"] == 40
    assert get_response.json()["implicit_cash_weight"] == pytest.approx(0.6)

    rebalance = client.get("/api/portfolio/rebalance")
    assert rebalance.status_code == 200
    assert rebalance.json()["has_targets"] is True
    assert rebalance.json()["disclaimer"]

    whatif = client.post("/api/portfolio/whatif", json={"amount": 100})
    assert whatif.status_code == 200
    assert whatif.json()["has_targets"] is True

    invalid = client.post("/api/portfolio/whatif", json={"amount": 0})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["message"] == "Contribution amount must be a positive number."


def test_nav_correlation_regime_tax_performance_twr_and_tearsheet(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    async def fake_value_holdings() -> PortfolioValuation:
        return _valuation()

    async def fake_compute_correlation_insight(symbols):
        return CorrelationInsight(
            symbols=symbols,
            avg_correlation=0.5,
            high_pairs=[],
            level="moderate",
            note="ok",
            matrix={"AAA": {"AAA": 1.0, "BBB": 0.5}, "BBB": {"AAA": 0.5, "BBB": 1.0}},
        )

    async def fake_compute_regime_signal(weights):
        return RegimeSignal(
            short_vol=0.3,
            long_vol=0.15,
            vol_ratio=2.0,
            level="elevated",
            note="risk",
            sample_days=180,
            asof="2026-07-05T00:00:00Z",
        )

    async def fake_compute_performance(weights):
        return PerformanceMetrics(
            cagr=0.1,
            total_return=0.2,
            sharpe=1.0,
            sortino=1.2,
            calmar=0.8,
            volatility=0.15,
            max_drawdown=-0.1,
            benchmark="SPY",
            benchmark_cagr=0.08,
            lookback_days=730,
            asof="2026-07-05T00:00:00Z",
        )

    async def fake_compute_twr_summary():
        return TWRSummary(
            twr_cumulative=0.12,
            twr_annualized=None,
            window_start="2026-01-01",
            window_end="2026-02-01",
            num_periods=2,
            benchmark="SPY",
            benchmark_cumulative=0.08,
            excess_cumulative=0.04,
            note="TWR strips out deposit and withdrawal timing.",
        )

    monkeypatch.setattr(api, "value_holdings", fake_value_holdings)
    monkeypatch.setattr(api, "compute_correlation_insight", fake_compute_correlation_insight)
    monkeypatch.setattr(api, "compute_regime_signal", fake_compute_regime_signal)
    monkeypatch.setattr(api, "compute_performance", fake_compute_performance)
    monkeypatch.setattr(api, "compute_twr_summary", fake_compute_twr_summary)

    with db.connect() as c:
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

    nav = client.get("/api/portfolio/nav")
    assert nav.status_code == 200
    assert nav.json()["series"]["points"][-1]["day"] == "2026-07-02"

    correlation = client.get("/api/portfolio/correlation")
    assert correlation.status_code == 200
    assert correlation.json()["symbols"] == ["AAA", "BBB"]
    assert correlation.json()["too_few"] is False

    regime = client.get("/api/portfolio/regime")
    assert regime.status_code == 200
    assert regime.json()["signal"]["level"] == "elevated"

    tax = client.get("/api/portfolio/tax")
    assert tax.status_code == 200
    assert "harvest_candidates" in tax.json()

    performance = client.get("/api/portfolio/performance")
    assert performance.status_code == 200
    assert performance.json()["metrics"]["calmar"] == 0.8

    twr = client.get("/api/portfolio/twr")
    assert twr.status_code == 200
    assert twr.json()["benchmark"] == "SPY"

    monkeypatch.setattr(api, "tearsheet_html", lambda weights: "<html>tear</html>")
    tearsheet = client.get("/api/portfolio/tearsheet")
    assert tearsheet.status_code == 200
    assert "tear" in tearsheet.text


def test_optimize_happy_path_empty_store_and_broker_sync(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    def fake_optimize(req):
        return _optimizer_result([h.symbol for h in req.holdings])

    async def fake_run_sync(dry_run=False):
        db.set_setting("last_broker_sync", "2026-07-06")
        return SyncResult(
            applied=True,
            diff=HoldingsDiff(
                to_upsert=[],
                to_remove=[],
                cash_before=0,
                cash_after=0,
                unchanged=0,
            ),
            imported_activities=0,
            skipped_activities=0,
            warnings=[],
            asof="2026-07-05T00:00:00Z",
        )

    monkeypatch.setattr(api, "optimize", fake_optimize)
    monkeypatch.setattr(api, "run_sync", fake_run_sync)

    empty = client.post("/api/portfolio/optimize", json={})
    assert empty.status_code == 200
    assert empty.json() == {
        "available": False,
        "reason": "No symbols provided.",
        "warnings": [],
        "result": None,
        "drift": None,
    }

    response = client.post(
        "/api/portfolio/optimize",
        json={
            "holdings": [
                {"symbol": "AAA", "value": 1000, "price": 25},
                {"symbol": "BBB", "value": 1000, "price": 30},
            ],
            "objective": "max_sharpe",
        },
    )
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["result"]["symbols"] == ["AAA", "BBB"]

    sync = client.post("/api/portfolio/broker/sync")
    assert sync.status_code == 200
    assert sync.json()["last_sync"] == "2026-07-06"
    assert sync.json()["result"]["applied"] is True


def test_optimize_excludes_penny_symbols_and_uses_remaining_holdings(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    def fake_optimize(req):
        return _optimizer_result([h.symbol for h in req.holdings])

    monkeypatch.setattr(api, "optimize", fake_optimize)

    response = client.post(
        "/api/portfolio/optimize",
        json={
            "holdings": [
                {"symbol": "AAA", "value": 1000, "price": 25},
                {"symbol": "PENY", "value": 1000, "price": 4},
                {"symbol": "BBB", "value": 1000, "price": 30},
            ],
            "objective": "max_sharpe",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["result"]["symbols"] == ["AAA", "BBB"]
    assert payload["warnings"] == [f"PENY: {api.OPTIMIZER_EXCLUSION_WARNING}"]


def test_optimize_penny_exclusion_leaves_too_few_symbols(client: TestClient):
    response = client.post(
        "/api/portfolio/optimize",
        json={
            "holdings": [
                {"symbol": "PENY", "value": 1000, "price": 4},
                {"symbol": "AAA", "value": 1000, "price": 25},
            ],
            "objective": "max_sharpe",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["reason"] == api.OPTIMIZER_EXCLUSION_WARNING
    assert payload["warnings"] == [f"PENY: {api.OPTIMIZER_EXCLUSION_WARNING}"]


def test_optimize_no_data_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    def fake_optimize(req):
        raise NoDataError("no data")

    monkeypatch.setattr(api, "optimize", fake_optimize)

    response = client.post(
        "/api/portfolio/optimize",
        json={
            "holdings": [
                {"symbol": "AAA", "value": 1000, "price": 25},
                {"symbol": "BBB", "value": 1000, "price": 30},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["reason"] == "no data"


def test_discover_budget_exceeded_returns_429(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    async def fake_discover_ideas(goal: str):
        raise BudgetExceededError(5.0, 5.0)

    monkeypatch.setattr(api, "discover_ideas", fake_discover_ideas)

    response = client.post("/api/discover", json={"goal": "ideas"})

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "budget_exceeded"


def test_broker_sync_failure_returns_internal_envelope(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
):
    async def fake_run_sync(dry_run=False):
        raise RuntimeError("sync exploded")

    monkeypatch.setattr(api, "run_sync", fake_run_sync)

    response = client.post("/api/portfolio/broker/sync")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "internal",
        "message": "Broker sync failed — see server logs.",
    }


def test_portfolio_snapshot_warns_not_fails(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    calls = []

    async def fake_value_holdings() -> PortfolioValuation:
        return _valuation()

    def fake_record_snapshot(valuation: PortfolioValuation) -> bool:
        calls.append(valuation.total_with_cash)
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(api, "value_holdings", fake_value_holdings)
    monkeypatch.setattr(api, "record_snapshot", fake_record_snapshot)

    response = client.get("/api/portfolio")

    assert response.status_code == 200
    assert calls == [250]
