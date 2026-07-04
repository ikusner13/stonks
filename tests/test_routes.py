from fastapi.testclient import TestClient

from app.llm.pipeline import InsufficientDataError
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
