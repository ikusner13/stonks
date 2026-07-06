from datetime import date, timedelta

from fastapi.testclient import TestClient
from pytest import approx

from app.portfolio.snapshots import NavSnapshot
from app.portfolio.twr import TWRSummary, compute_twr
from app.web import api
from app.web import app as web_app


def _snapshot(day: str, nav: float) -> NavSnapshot:
    return NavSnapshot(
        day=day,
        total_value=nav,
        cash=0,
        total_with_cash=nav,
        total_cost=0,
        unrealized_pl=0,
    )


def test_compute_twr_no_flows_chains_returns():
    snapshots = [
        _snapshot("2026-01-01", 100),
        _snapshot("2026-01-16", 110),
        _snapshot("2026-02-01", 121),
    ]

    result = compute_twr(snapshots, [])

    assert result is not None
    twr, periods, start, end = result
    assert twr == approx(0.21)
    assert periods == 2
    assert start == "2026-01-01"
    assert end == "2026-02-01"


def test_compute_twr_deposit_mid_window_strips_flow_timing():
    snapshots = [_snapshot("2026-01-01", 100), _snapshot("2026-01-16", 210)]

    result = compute_twr(snapshots, [("2026-01-16", 100)])

    assert result is not None
    assert result[0] == approx(0.1)


def test_compute_twr_withdrawal_mid_window_strips_flow_timing():
    snapshots = [_snapshot("2026-01-01", 100), _snapshot("2026-01-16", 90)]

    result = compute_twr(snapshots, [("2026-01-16", -20)])

    assert result is not None
    assert result[0] == approx(0.1)


def test_compute_twr_flow_on_snapshot_day_belongs_to_period_ending_that_day():
    snapshots = [
        _snapshot("2026-01-01", 100),
        _snapshot("2026-01-16", 210),
        _snapshot("2026-02-01", 231),
    ]

    result = compute_twr(snapshots, [("2026-01-16", 100)])

    assert result is not None
    assert result[0] == approx(0.21)


def test_compute_twr_rejects_invalid_or_insufficient_windows():
    assert compute_twr([_snapshot("2026-01-01", 100)], []) is None
    assert compute_twr([_snapshot("2026-01-01", 100), _snapshot("2026-01-07", 110)], []) is None
    assert compute_twr([_snapshot("2026-01-01", 0), _snapshot("2026-01-16", 110)], []) is None
    assert compute_twr([_snapshot("2026-01-01", 100), _snapshot("2026-01-16", -1)], []) is None


def test_twr_annualized_none_under_365_days():
    start = date(2026, 1, 1)
    snapshots = [_snapshot(start.isoformat(), 100), _snapshot((start + timedelta(days=31)).isoformat(), 110)]

    result = compute_twr(snapshots, [])

    assert result is not None
    twr, _, window_start, window_end = result
    span = (date.fromisoformat(window_end) - date.fromisoformat(window_start)).days
    annualized = (1 + twr) ** (365.25 / span) - 1 if span >= 365 else None
    assert annualized is None


def test_twr_api_returns_summary(monkeypatch):
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

    monkeypatch.setattr(api, "compute_twr_summary", fake_compute_twr_summary)
    client = TestClient(web_app.app)

    response = client.get("/api/portfolio/twr")

    assert response.status_code == 200
    payload = response.json()
    assert payload["twr_cumulative"] == 0.12
    assert payload["benchmark"] == "SPY"
    assert payload["excess_cumulative"] == 0.04
    assert payload["note"] == "TWR strips out deposit and withdrawal timing."
