from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "REGIME_ALERT_ENABLED", False)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "BACKUP_DIR", None)
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


def _weighted_valuation() -> PortfolioValuation:
    valuation = _valuation()
    valuation.holdings[0].weight = 0.27
    valuation.holdings[1].weight = 0.73
    return valuation


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


def test_is_due_for_pinned_hour_jobs():
    job = jobs.Job(name="daily", run=lambda: None, at_hour_utc=21)
    now = datetime(2026, 7, 5, 20, 30, tzinfo=UTC)

    assert jobs.is_due(job, now, None) is False
    assert jobs.is_due(job, datetime(2026, 7, 5, 21, 0, tzinfo=UTC), None) is True
    assert (
        jobs.is_due(
            job,
            datetime(2026, 7, 5, 22, 0, tzinfo=UTC),
            datetime(2026, 7, 5, 21, 0, tzinfo=UTC),
        )
        is False
    )
    assert (
        jobs.is_due(
            job,
            datetime(2026, 7, 5, 22, 0, tzinfo=UTC),
            datetime(2026, 7, 2, 21, 0, tzinfo=UTC),
        )
        is True
    )


def test_is_due_for_cadence_jobs():
    job = jobs.Job(name="interval", run=lambda: None, cadence=timedelta(hours=6))
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)

    assert jobs.is_due(job, now, None) is True
    assert jobs.is_due(job, now, datetime(2026, 7, 5, 9, 1, tzinfo=UTC)) is False
    assert jobs.is_due(job, now, datetime(2026, 7, 5, 6, 0, tzinfo=UTC)) is True


def test_job_requires_exactly_one_schedule():
    async def run():
        return None

    with pytest.raises(ValueError):
        jobs.Job(name="none", run=run)
    with pytest.raises(ValueError):
        jobs.Job(name="both", run=run, at_hour_utc=21, cadence=timedelta(hours=1))


async def test_run_due_jobs_updates_successful_jobs_and_continues_after_failure():
    calls: list[str] = []

    async def successful():
        calls.append("successful")

    async def failing():
        calls.append("failing")
        raise RuntimeError("boom")

    async def after_failure():
        calls.append("after_failure")

    async def not_due():
        calls.append("not_due")

    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    db.set_setting(f"{jobs.LAST_RUN_PREFIX}successful", "not-a-date")
    db.set_setting(f"{jobs.LAST_RUN_PREFIX}not_due", now.isoformat())
    registry = [
        jobs.Job(name="successful", run=successful, cadence=timedelta(hours=1)),
        jobs.Job(name="failing", run=failing, cadence=timedelta(hours=1)),
        jobs.Job(name="after_failure", run=after_failure, cadence=timedelta(hours=1)),
        jobs.Job(name="not_due", run=not_due, cadence=timedelta(hours=1)),
    ]

    result = await jobs.run_due_jobs(registry, now)

    assert result == {"successful": True, "failing": False, "after_failure": True}
    assert calls == ["successful", "failing", "after_failure"]
    assert db.get_setting(f"{jobs.LAST_RUN_PREFIX}successful") == now.isoformat()
    assert db.get_setting(f"{jobs.LAST_RUN_PREFIX}failing") is None
    assert db.get_setting(f"{jobs.LAST_RUN_PREFIX}after_failure") == now.isoformat()
    assert db.get_setting(f"{jobs.LAST_RUN_PREFIX}not_due") == now.isoformat()


def test_build_jobs_empty_when_daily_hour_negative(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", -1)
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)

    assert jobs.build_jobs() == []


def test_build_jobs_registers_sec_alerts_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", -1)
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "SEC_ALERT_HOURS", 4)

    registry = jobs.build_jobs()

    assert len(registry) == 1
    assert registry[0].name == "sec_filing_alerts"
    assert registry[0].cadence == timedelta(hours=4)


def test_build_jobs_registers_alert_jobs_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", -1)
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(config, "ALERTS_HOUR_UTC", 22)

    registry = jobs.build_jobs()

    assert [job.name for job in registry] == ["price_alerts", "earnings_alerts"]
    assert [job.at_hour_utc for job in registry] == [22, 22]


def test_build_jobs_registers_regime_alert_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", -1)
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "REGIME_ALERT_ENABLED", True)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(config, "ALERTS_HOUR_UTC", 22)

    registry = jobs.build_jobs()

    assert [job.name for job in registry] == ["regime_alert"]
    assert registry[0].at_hour_utc == 22


def test_build_jobs_registers_backup_when_configured(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", 21)
    monkeypatch.setattr(config, "ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", False)
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))

    registry = jobs.build_jobs()

    assert [job.name for job in registry] == ["daily_portfolio", "db_backup"]
    assert [job.at_hour_utc for job in registry] == [21, 21]


def test_build_jobs_can_register_phase2_and_sec_alerts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DAILY_JOB_HOUR_UTC", -1)
    monkeypatch.setattr(config, "ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "SEC_ALERTS_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(config, "ALERTS_HOUR_UTC", 22)
    monkeypatch.setattr(config, "SEC_ALERT_HOURS", 4)

    registry = jobs.build_jobs()

    assert [job.name for job in registry] == [
        "price_alerts",
        "earnings_alerts",
        "sec_filing_alerts",
    ]
    assert [job.at_hour_utc for job in registry] == [22, 22, None]
    assert registry[2].cadence == timedelta(hours=4)


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


async def test_run_daily_jobs_returns_when_alert_planning_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _valuation()

    def list_targets():
        raise RuntimeError("target db unavailable")

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "record_snapshot", lambda valuation: True)
    monkeypatch.setattr(jobs, "list_targets", list_targets)
    _install_http_recorder(monkeypatch, posts)

    result = await jobs.run_daily_jobs()

    assert result == {"snapshot": True, "alert": ""}
    assert posts == []
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


async def test_run_regime_alert_transition_into_elevated_fires_once(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _weighted_valuation()

    async def compute_regime_signal(weights):
        assert weights == {"AAA": 0.27, "BBB": 0.73}
        return SimpleNamespace(level="elevated", vol_ratio=1.8, short_vol=0.32, long_vol=0.18)

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "compute_regime_signal", compute_regime_signal)
    _install_http_recorder(monkeypatch, posts)

    first = await jobs.run_regime_alert()
    second = await jobs.run_regime_alert()

    assert first["alert"] == (
        "Volatility regime: ELEVATED — recent swings ~1.8x this portfolio's norm "
        "(32% vs 18% annualized). Cautious time to add risk."
    )
    assert second == {"alert": ""}
    assert len(posts) == 1
    assert posts[0]["json"] == {"content": first["alert"]}
    assert db.get_setting(jobs.LAST_REGIME_LEVEL_KEY) == "elevated"


async def test_run_regime_alert_recovery_fires(monkeypatch: pytest.MonkeyPatch):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _weighted_valuation()

    async def compute_regime_signal(weights):
        return SimpleNamespace(level="normal", vol_ratio=1.1, short_vol=0.20, long_vol=0.18)

    db.set_setting(jobs.LAST_REGIME_LEVEL_KEY, "elevated")
    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "compute_regime_signal", compute_regime_signal)
    _install_http_recorder(monkeypatch, posts)

    result = await jobs.run_regime_alert()

    assert result == {"alert": "Volatility regime back to normal (1.1x norm)."}
    assert posts[0]["json"] == {"content": result["alert"]}
    assert db.get_setting(jobs.LAST_REGIME_LEVEL_KEY) == "normal"


async def test_run_regime_alert_none_signal_leaves_stored_state_untouched(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _weighted_valuation()

    async def compute_regime_signal(weights):
        return None

    db.set_setting(jobs.LAST_REGIME_LEVEL_KEY, "normal")
    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "compute_regime_signal", compute_regime_signal)
    _install_http_recorder(monkeypatch, posts)

    result = await jobs.run_regime_alert()

    assert result == {"alert": ""}
    assert posts == []
    assert db.get_setting(jobs.LAST_REGIME_LEVEL_KEY) == "normal"


async def test_run_regime_alert_webhook_failure_does_not_store_level(
    monkeypatch: pytest.MonkeyPatch,
):
    posts: list[dict] = []

    async def value_holdings() -> PortfolioValuation:
        return _weighted_valuation()

    async def compute_regime_signal(weights):
        return SimpleNamespace(level="elevated", vol_ratio=1.8, short_vol=0.32, long_vol=0.18)

    monkeypatch.setattr(jobs, "value_holdings", value_holdings)
    monkeypatch.setattr(jobs, "compute_regime_signal", compute_regime_signal)
    _install_http_recorder(monkeypatch, posts, raises=True)

    result = await jobs.run_regime_alert()

    assert result == {"alert": ""}
    assert len(posts) == 1
    assert db.get_setting(jobs.LAST_REGIME_LEVEL_KEY) is None
