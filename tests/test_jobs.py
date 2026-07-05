from datetime import UTC, datetime

import pytest

from app import config, db
from app import jobs
from app.portfolio.holdings import HoldingValuation, PortfolioValuation
from app.portfolio.plan import Target, init_targets_db, set_targets


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(config, "DRIFT_ALERT_ENABLED", True)
    db.init_db()
    init_targets_db()


def _holding(symbol: str, value: float, price: float = 100) -> HoldingValuation:
    return HoldingValuation(
        symbol=symbol,
        shares=value / price,
        avg_cost=None,
        price=price,
        market_value=value,
        cost_value=None,
        unrealized_pl=None,
        unrealized_pl_pct=None,
        weight=None,
    )


def _valuation() -> PortfolioValuation:
    return PortfolioValuation(
        holdings=[
            _holding("AAA", 2700),
            _holding("BBB", 7300),
        ],
        total_value=10_000,
        total_cost=0,
        total_unrealized_pl=0,
        total_unrealized_pl_pct=0,
        asof="2026-07-05T00:00:00+00:00",
        cash=0,
        total_with_cash=10_000,
        cash_pct=0,
    )


def _install_http_recorder(monkeypatch: pytest.MonkeyPatch, posts: list[dict], *, raises=False):
    class FakeResponse:
        def raise_for_status(self) -> None:
            if raises:
                raise RuntimeError("post failed")

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, json):
            posts.append({"url": url, "json": json, "timeout": self.timeout})
            return FakeResponse()

    monkeypatch.setattr(jobs.httpx, "AsyncClient", FakeAsyncClient)


def test_seconds_until_next_before_after_and_exact_hour():
    assert jobs.seconds_until_next(21, datetime(2026, 7, 5, 20, 30, tzinfo=UTC)) == 1800
    assert jobs.seconds_until_next(21, datetime(2026, 7, 5, 21, 0, tzinfo=UTC)) == 86400
    assert jobs.seconds_until_next(21, datetime(2026, 7, 5, 22, 0, tzinfo=UTC)) == 82800


async def test_run_daily_jobs_records_snapshot_sends_dedupes_and_resends_changed(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []
    snapshots: list[PortfolioValuation] = []

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    def record_snapshot(valuation: PortfolioValuation) -> bool:
        snapshots.append(valuation)
        return True

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "record_snapshot", record_snapshot)
    _install_http_recorder(monkeypatch, posts)

    set_targets([Target(symbol="AAA", target_weight=0.20), Target(symbol="BBB", target_weight=0.80)])
    first = await jobs.run_daily_jobs()

    assert first["snapshot"] is True
    assert "Rebalance drift: AAA 27.0% vs target 20.0% \u2192 sell $700 (~7 sh)" in first["alert"]
    assert "cash after: $0" in first["alert"]
    assert len(snapshots) == 1
    assert len(posts) == 1
    assert posts[0]["url"] == "https://discord.test/webhook"
    assert posts[0]["timeout"] == 5
    assert posts[0]["json"] == {"content": first["alert"]}
    assert db.get_setting(jobs.LAST_DRIFT_ALERT_KEY).endswith(":AAA,BBB")

    second = await jobs.run_daily_jobs()

    assert second["alert"] == ""
    assert len(posts) == 1

    set_targets([
        Target(symbol="AAA", target_weight=0.20),
        Target(symbol="BBB", target_weight=0.70),
        Target(symbol="CCC", target_weight=0.10),
    ])
    changed = await jobs.run_daily_jobs()

    assert "CCC" in changed["alert"]
    assert len(posts) == 2
    assert db.get_setting(jobs.LAST_DRIFT_ALERT_KEY).endswith(":AAA,CCC")


async def test_run_daily_jobs_skips_alert_when_webhook_unset(monkeypatch: pytest.MonkeyPatch):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "record_snapshot", lambda valuation: True)
    _install_http_recorder(monkeypatch, posts)
    set_targets([Target(symbol="AAA", target_weight=0.20), Target(symbol="BBB", target_weight=0.80)])

    result = await jobs.run_daily_jobs()

    assert result == {"snapshot": True, "alert": ""}
    assert posts == []
    assert db.get_setting(jobs.LAST_DRIFT_ALERT_KEY) is None


async def test_run_daily_jobs_does_not_update_dedupe_when_webhook_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "record_snapshot", lambda valuation: True)
    _install_http_recorder(monkeypatch, posts, raises=True)
    set_targets([Target(symbol="AAA", target_weight=0.20), Target(symbol="BBB", target_weight=0.80)])

    result = await jobs.run_daily_jobs()

    assert result == {"snapshot": True, "alert": ""}
    assert len(posts) == 1
    assert db.get_setting(jobs.LAST_DRIFT_ALERT_KEY) is None


async def test_run_daily_jobs_returns_without_alert_when_valuation_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    def record_snapshot(valuation: PortfolioValuation) -> bool:
        raise AssertionError("snapshot should not run")

    async def value_holdings() -> PortfolioValuation:
        raise RuntimeError("valuation failed")

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "record_snapshot", record_snapshot)

    result = await jobs.run_daily_jobs()

    assert result == {"snapshot": False, "alert": ""}
