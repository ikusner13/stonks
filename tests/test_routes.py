from fastapi.testclient import TestClient

from app import config, db
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.llm.pipeline import InsufficientDataError
from app.portfolio import holdings as holdings_mod
from app.portfolio.decision_support import PositionSizeGuidance
from app.portfolio.holdings import PortfolioValuation
from app.portfolio.optimize import OptimizeResult, PortfolioMetrics
from app.schemas import Critique, FabricationCheck, ResearchResult, Thesis, TickerData, TickerReport
from app.web import app as web_app


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


def _empty_valuation() -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[],
        total_value=100_000,
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        cash=0.0,
        total_with_cash=100_000,
        asof="2026-07-05T00:00:00Z",
    )


def _optimizer_result(symbols: list[str]) -> OptimizeResult:
    weights = {symbol: round(1 / len(symbols), 6) for symbol in symbols}
    metrics = PortfolioMetrics(
        weights=weights,
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
        efficient_frontier=[],
        warnings=[],
    )


def test_research_report_runtime_error_returns_error_partial(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(web_app, "research_ticker_cached", fail)
    client = TestClient(web_app.app)

    response = client.get("/research/AAPL/report")

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "Research failed" in response.text
    assert "Retry" in response.text


def test_research_report_insufficient_data_names_symbol(monkeypatch):
    async def fail(*args, **kwargs):
        raise InsufficientDataError("AAPL", {"quote": "empty"})

    monkeypatch.setattr(web_app, "research_ticker_cached", fail)
    client = TestClient(web_app.app)

    response = client.get("/research/AAPL/report")

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "No market data found for AAPL" in response.text
    assert "Retry" not in response.text


def test_research_report_profile_override_reaches_pipeline_and_sizing(monkeypatch):
    seen = {}

    async def fake_research_ticker_cached(symbol, mode, *, profile_override=None, fresh=False):
        seen["pipeline"] = (symbol, mode, profile_override, fresh)
        return _research_result(profile="penny")

    async def fake_value_holdings():
        return _empty_valuation()

    def fake_suggest_position_size(
        portfolio_value,
        confidence,
        symbol=None,
        *,
        current_weight=None,
        profile,
        adv_dollars=None,
    ):
        seen["sizing"] = (portfolio_value, confidence, symbol, current_weight, profile.key, adv_dollars)
        return PositionSizeGuidance(
            symbol=symbol,
            confidence=confidence,
            portfolio_value=portfolio_value,
            low_pct=0.01,
            high_pct=0.03,
            low_dollars=1000,
            high_dollars=3000,
            note="ok",
            profile=profile.key,
        )

    monkeypatch.setattr(web_app, "research_ticker_cached", fake_research_ticker_cached)
    monkeypatch.setattr(web_app, "value_holdings", fake_value_holdings)
    monkeypatch.setattr(web_app, "suggest_position_size", fake_suggest_position_size)
    client = TestClient(web_app.app)

    response = client.get("/research/PENY/report?mode=cheap&profile=penny")

    assert response.status_code == 200
    assert seen["pipeline"] == ("PENY", "cheap", "penny", False)
    assert seen["sizing"] == (100_000, "medium", "PENY", None, "penny", 530_000.0)
    assert "profile Penny / micro-cap" in response.text
    assert "manual override" in response.text
    assert "$530K" in response.text


def test_research_report_invalid_profile_returns_422():
    client = TestClient(web_app.app)

    response = client.get("/research/AAPL/report?profile=bad")

    assert response.status_code == 422


def test_indicator_value_formatter_formats_usd_and_count():
    assert web_app._fmt_indicator_value(1_234_567, "usd") == "$1.2M"
    assert web_app._fmt_indicator_value(530_000, "usd") == "$530K"
    assert web_app._fmt_indicator_value(123_456_789.0, "count") == "123,456,789"


def test_optimizer_excludes_sub_five_holding_with_warning(monkeypatch):
    seen = {}

    def fake_optimize(req):
        seen["symbols"] = [h.symbol for h in req.holdings]
        return _optimizer_result(seen["symbols"])

    monkeypatch.setattr(web_app, "optimize", fake_optimize)
    client = TestClient(web_app.app)

    response = client.post(
        "/portfolio/optimize",
        data={
            "symbol": ["AAA", "PENY", "BBB"],
            "value": ["1000", "500", "1000"],
            "price": ["25", "4.99", "30"],
            "objective": "max_sharpe",
        },
    )

    assert response.status_code == 200
    assert seen["symbols"] == ["AAA", "BBB"]
    assert (
        "PENY: excluded from mean-variance optimization: sample statistics on illiquid "
        "micro-caps are unreliable"
    ) in response.text
    assert "Optimal (max_sharpe)" in response.text


def test_optimizer_skips_when_fewer_than_two_symbols_remain(monkeypatch):
    def fail_optimize(req):
        raise AssertionError("optimizer should not run")

    monkeypatch.setattr(web_app, "optimize", fail_optimize)
    client = TestClient(web_app.app)

    response = client.post(
        "/portfolio/optimize",
        data={
            "symbol": ["AAA", "PENY"],
            "value": ["1000", "500"],
            "price": ["25", "4.99"],
            "objective": "max_sharpe",
        },
    )

    assert response.status_code == 200
    assert "excluded from mean-variance optimization" in response.text
    assert "sample statistics on illiquid micro-caps are unreliable" in response.text
    assert "PENY:" in response.text


def test_portfolio_cash_post_updates_cash_and_ignores_garbage(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    holdings_mod.init_holdings_db()
    client = TestClient(web_app.app)

    response = client.post("/portfolio/cash", data={"cash": "123.45"})

    assert response.status_code == 200
    assert db.get_cash() == 123.45
    assert "Cash" in response.text
    assert "$123.45" in response.text
    assert "Total (incl. cash)" in response.text
    assert "100.0%" in response.text

    response = client.post("/portfolio/cash", data={"cash": "garbage"})

    assert response.status_code == 200
    assert db.get_cash() == 123.45
    assert "$123.45" in response.text
