from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from app import alerts, config, db
from app.portfolio.holdings import init_holdings_db, upsert_holding
from app.schemas import Quote


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "stocks.db")
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setattr(config, "PRICE_MOVE_ALERT_PCT", 5.0)
    monkeypatch.setattr(config, "EARNINGS_ALERT_DAYS", 7)
    db.init_db()
    init_holdings_db()
    alerts.init_alerts_db()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch):
    async def sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(alerts.asyncio, "sleep", sleep)


def _quote(price: float, change_percent: float) -> Quote:
    return Quote(price=price, currency="USD", change=0.0, change_percent=change_percent)


def _range_row(symbol: str) -> tuple[float, float] | None:
    with db.connect() as c:
        row = c.execute(
            "SELECT high, low FROM price_ranges WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    return None if row is None else (row["high"], row["low"])


def _sent(kind: str, key: str) -> bool:
    with db.connect() as c:
        row = c.execute(
            "SELECT 1 FROM alerts_sent WHERE kind = ? AND dedupe_key = ?",
            (kind, key),
        ).fetchone()
    return row is not None


def test_alert_universe_dedupes_uppercases_and_sorts():
    upsert_holding("aapl", 1, None)
    upsert_holding("MSFT", 1, None)
    db.add("msft")
    db.add("tsla")

    assert alerts.alert_universe() == ["AAPL", "MSFT", "TSLA"]


async def test_price_move_fires_at_threshold_and_dedupes(monkeypatch: pytest.MonkeyPatch):
    db.add("AAPL")
    posts: list[str] = []

    def fetch_price_history(_symbols: list[str], _lookback_days: int):
        return pd.DataFrame({"AAPL": [90.0, 100.0]}), []

    async def fetch_quote(symbol: str) -> Quote | None:
        assert symbol == "AAPL"
        return _quote(99.0, 5.0)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "fetch_price_history", fetch_price_history)
    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)
    monkeypatch.setattr(alerts, "post_discord", post_discord)

    first = await alerts.run_price_alerts()
    second = await alerts.run_price_alerts()

    today = datetime.now(UTC).date().isoformat()
    assert first == {"alerts": 1}
    assert second == {"alerts": 0}
    assert posts == ["AAPL +5.0% today ($99.00)"]
    assert _sent("price_move", f"AAPL:{today}") is True


async def test_price_move_below_threshold_is_ignored(monkeypatch: pytest.MonkeyPatch):
    db.add("AAPL")
    posts: list[str] = []

    monkeypatch.setattr(
        alerts,
        "fetch_price_history",
        lambda _symbols, _lookback_days: (pd.DataFrame({"AAPL": [90.0, 100.0]}), []),
    )

    async def fetch_quote(_symbol: str) -> Quote | None:
        return _quote(99.0, 4.9)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)
    monkeypatch.setattr(alerts, "post_discord", post_discord)

    result = await alerts.run_price_alerts()

    assert result == {"alerts": 0}
    assert posts == []


async def test_range_new_high_updates_row_and_alerts(monkeypatch: pytest.MonkeyPatch):
    db.add("AAPL")
    posts: list[str] = []

    monkeypatch.setattr(
        alerts,
        "fetch_price_history",
        lambda _symbols, _lookback_days: (pd.DataFrame({"AAPL": [90.0, 100.0]}), []),
    )

    async def fetch_quote(_symbol: str) -> Quote | None:
        return _quote(110.0, 1.0)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)
    monkeypatch.setattr(alerts, "post_discord", post_discord)

    result = await alerts.run_price_alerts()

    assert result == {"alerts": 1}
    assert posts == ["AAPL new 52-week high $110.00 (prev $100.00)"]
    assert _range_row("AAPL") == (110.0, 90.0)


async def test_missing_range_skips_range_alert_but_allows_move(monkeypatch: pytest.MonkeyPatch):
    db.add("AAPL")
    posts: list[str] = []

    def fetch_price_history(_symbols: list[str], _lookback_days: int):
        return pd.DataFrame(), []

    async def fetch_quote(_symbol: str) -> Quote | None:
        return _quote(110.0, 6.2)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "fetch_price_history", fetch_price_history)
    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)
    monkeypatch.setattr(alerts, "post_discord", post_discord)

    result = await alerts.run_price_alerts()

    assert result == {"alerts": 1}
    assert posts == ["AAPL +6.2% today ($110.00)"]
    assert _range_row("AAPL") is None


async def test_price_history_failure_keeps_stale_range_and_job_returns(
    monkeypatch: pytest.MonkeyPatch,
):
    db.add("AAPL")
    alerts._upsert_range("AAPL", 100.0, 90.0, "2026-01-01")
    posts: list[str] = []

    def fetch_price_history(_symbols: list[str], _lookback_days: int):
        raise RuntimeError("yfinance down")

    async def fetch_quote(_symbol: str) -> Quote | None:
        return _quote(89.0, 1.0)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "fetch_price_history", fetch_price_history)
    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)
    monkeypatch.setattr(alerts, "post_discord", post_discord)

    result = await alerts.run_price_alerts()

    assert result == {"alerts": 1}
    assert posts == ["AAPL new 52-week low $89.00 (prev $90.00)"]
    assert _range_row("AAPL") == (100.0, 89.0)


async def test_webhook_failure_does_not_mark_sent_and_next_run_resends(
    monkeypatch: pytest.MonkeyPatch,
):
    db.add("AAPL")
    posts: list[str] = []
    fail = True

    monkeypatch.setattr(
        alerts,
        "fetch_price_history",
        lambda _symbols, _lookback_days: (pd.DataFrame({"AAPL": [90.0, 100.0]}), []),
    )

    async def fetch_quote(_symbol: str) -> Quote | None:
        return _quote(99.0, 5.1)

    monkeypatch.setattr(alerts, "fetch_quote", fetch_quote)

    async def post_discord(message: str) -> None:
        nonlocal fail
        posts.append(message)
        if fail:
            fail = False
            raise RuntimeError("webhook down")

    monkeypatch.setattr(alerts, "post_discord", post_discord)

    first = await alerts.run_price_alerts()
    second = await alerts.run_price_alerts()

    assert first == {"alerts": 0}
    assert second == {"alerts": 1}
    assert posts == [
        "AAPL +5.1% today ($99.00)",
        "AAPL +5.1% today ($99.00)",
    ]


async def test_earnings_alerts_inside_window_once_and_ignores_outside(
    monkeypatch: pytest.MonkeyPatch,
):
    db.add("AAPL")
    today = datetime.now(UTC).date()
    inside = today + timedelta(days=5)
    outside = today + timedelta(days=8)
    posts: list[str] = []

    async def fetch_earnings_calendar(symbol: str, from_date: date, to_date: date):
        assert symbol == "AAPL"
        assert from_date == today
        assert to_date == today + timedelta(days=7)
        return [{"date": inside.isoformat()}, {"date": outside.isoformat()}]

    monkeypatch.setattr(alerts, "fetch_earnings_calendar", fetch_earnings_calendar)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "post_discord", post_discord)

    first = await alerts.run_earnings_alerts()
    second = await alerts.run_earnings_alerts()

    assert first == {"alerts": 1}
    assert second == {"alerts": 0}
    assert posts == [f"AAPL earnings {inside.isoformat()} (in 5d)"]


async def test_earnings_none_calendar_is_clean_noop(monkeypatch: pytest.MonkeyPatch):
    db.add("AAPL")
    posts: list[str] = []

    async def fetch_earnings_calendar(_symbol: str, _from_date: date, _to_date: date):
        return None

    monkeypatch.setattr(alerts, "fetch_earnings_calendar", fetch_earnings_calendar)

    async def post_discord(message: str) -> None:
        posts.append(message)

    monkeypatch.setattr(alerts, "post_discord", post_discord)

    result = await alerts.run_earnings_alerts()

    assert result == {"alerts": 0}
    assert posts == []
