import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import config
from app.llm import budget, usage
from app.llm.budget import BudgetExceededError
from app.llm import pipeline
from app.web import app as web_app


def _cached_research_value():
    return {
        "ticker": {
            "symbol": "AAPL",
            "fetched_at": "2026-07-04T00:00:00Z",
            "fundamentals": {},
            "news": [],
        },
        "report": {
            "symbol": "AAPL",
            "company_name": "Apple Inc.",
            "summary": "Cached summary.",
            "thesis": {"bull": [], "bear": []},
            "key_metrics": [],
            "valuation_context": "",
            "risks": [],
            "things_to_investigate": [],
            "confidence": "low",
        },
        "critique": {
            "fabrication_check": {"passed": True, "details": "ok"},
            "issues": [],
            "suggested_confidence": "low",
            "overall_assessment": "ok",
        },
        "revised": False,
    }


def test_spent_today_sums_today_only(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)
    lines = [
        {"ts": today.isoformat(), "totals": {"cost_usd": 1.25}},
        {"ts": f"{today.date().isoformat()}T23:59:59+00:00", "totals": {"cost_usd": 0.75}},
        {"ts": yesterday.isoformat(), "totals": {"cost_usd": 99}},
        "not json",
    ]
    log.write_text("\n".join(json.dumps(line) if isinstance(line, dict) else line for line in lines))
    monkeypatch.setattr(usage, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(usage, "USAGE_LOG", log)

    assert budget.spent_today() == 2.0


def test_check_budget_raises_at_or_above_limit(monkeypatch):
    monkeypatch.setattr(config, "DAILY_LLM_BUDGET_USD", 2.0)
    monkeypatch.setattr(budget, "spent_today", lambda: 2.0)

    try:
        budget.check_budget()
    except BudgetExceededError as e:
        assert e.spent == 2.0
        assert e.limit == 2.0
    else:
        raise AssertionError("expected BudgetExceededError")


def test_check_budget_passes_below_limit(monkeypatch):
    monkeypatch.setattr(config, "DAILY_LLM_BUDGET_USD", 2.0)
    monkeypatch.setattr(budget, "spent_today", lambda: 1.99)

    budget.check_budget()


def test_check_budget_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "DAILY_LLM_BUDGET_USD", 0.0)
    monkeypatch.setattr(
        budget,
        "spent_today",
        lambda: (_ for _ in ()).throw(AssertionError("spent_today should not run")),
    )

    budget.check_budget()


async def test_research_cache_hit_does_not_check_budget(monkeypatch):
    async def cache_hit(namespace, key, ttl_ms, produce, *, fresh=False):
        return _cached_research_value(), True

    monkeypatch.setattr(pipeline, "with_cache", cache_hit)
    monkeypatch.setattr(
        pipeline,
        "check_budget",
        lambda: (_ for _ in ()).throw(AssertionError("cache hit should not check budget")),
    )

    result = await pipeline.research_ticker_cached("AAPL")

    assert result.report.summary == "Cached summary."


def test_research_budget_error_returns_error_partial(monkeypatch):
    async def fail(*args, **kwargs):
        raise BudgetExceededError(5.0, 5.0)

    monkeypatch.setattr(web_app, "research_ticker_cached", fail)
    client = TestClient(web_app.app)

    response = client.get("/research/AAPL/report")

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "Daily LLM budget reached ($5.00 of $5.00)" in response.text
    assert "Resets at midnight UTC; cached reports still load." in response.text
    assert "Retry" not in response.text


def test_discover_budget_error_returns_error_partial(monkeypatch):
    async def fail(*args, **kwargs):
        raise BudgetExceededError(5.0, 5.0)

    monkeypatch.setattr(web_app, "discover_ideas", fail)
    client = TestClient(web_app.app)

    response = client.post("/discover", data={"goal": "small cap semis"})

    assert response.status_code == 200
    assert "error-panel" in response.text
    assert "Daily LLM budget reached ($5.00 of $5.00)" in response.text
    assert "Retry" not in response.text
