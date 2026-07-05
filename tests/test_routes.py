from fastapi.testclient import TestClient

from app import config, db
from app.llm.pipeline import InsufficientDataError
from app.portfolio import holdings as holdings_mod
from app.web import app as web_app


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
