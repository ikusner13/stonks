from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app import db
from app import ledger as ledger_mod
from app.schemas import (
    Critique,
    FabricationCheck,
    Fundamentals,
    Quote,
    ResearchResult,
    Thesis,
    TickerData,
    TickerReport,
)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "stocks.db")
    db.init_db()
    return tmp_path / "stocks.db"


def _result(
    *,
    symbol: str = "AAPL",
    fetched_at: str = "2020-01-02T12:00:00Z",
    stance: str | None = "bullish",
    confidence: str = "medium",
    price: float | None = 100.0,
) -> ResearchResult:
    return ResearchResult(
        ticker=TickerData(
            symbol=symbol,
            fetched_at=fetched_at,
            quote=(
                Quote(price=price, currency="USD", change=0, change_percent=0)
                if price is not None
                else None
            ),
            fundamentals=Fundamentals(),
        ),
        report=TickerReport(
            symbol=symbol,
            company_name=f"{symbol} Inc.",
            summary="A summary.",
            thesis=Thesis(bull=[], bear=[]),
            key_metrics=[],
            valuation_context="",
            risks=[],
            things_to_investigate=[],
            confidence=confidence,
            stance=stance,
        ),
        critique=Critique(
            fabrication_check=FabricationCheck(passed=True, details="ok"),
            issues=[],
            suggested_confidence=confidence,
            overall_assessment="ok",
        ),
        revised=False,
    )


def _call(
    *,
    stance: str | None,
    confidence: str = "medium",
    horizon: str = "1m",
    fwd: float = 0.0,
    bench: float = 0.0,
) -> ledger_mod.Call:
    return ledger_mod.Call(
        id=1,
        symbol="AAPL",
        as_of="2020-01-02",
        mode="thorough",
        stance=stance,
        confidence=confidence,
        price=100.0,
        revised=False,
        outcomes={
            horizon: ledger_mod.Outcome(
                horizon=horizon,
                fwd_return=fwd,
                bench_return=bench,
            )
        },
    )


def test_score_window_exact_date_base():
    closes = pd.Series(
        [100.0, 110.0],
        index=pd.to_datetime(["2020-01-02", "2020-01-09"]),
    )

    assert ledger_mod.score_window(closes, date(2020, 1, 2), 7) == pytest.approx(0.10)


def test_score_window_weekend_roll_forward():
    closes = pd.Series(
        [100.0, 120.0],
        index=pd.to_datetime(["2020-01-06", "2020-01-13"]),
    )

    assert ledger_mod.score_window(closes, date(2020, 1, 4), 7) == pytest.approx(0.20)


def test_score_window_unmatured_returns_none():
    closes = pd.Series([100.0], index=pd.to_datetime(["2020-01-02"]))

    assert ledger_mod.score_window(closes, date(2020, 1, 2), 7) is None


def test_score_window_missing_series_returns_none():
    closes = pd.Series(dtype=float)

    assert ledger_mod.score_window(closes, date(2020, 1, 2), 7) is None


def test_hit_rule_and_avg_excess_include_neutral_and_null():
    calls = [
        _call(stance="bullish", fwd=0.08, bench=0.03),
        _call(stance="bullish", fwd=0.01, bench=0.03),
        _call(stance="bearish", fwd=-0.03, bench=0.01),
        _call(stance="bearish", fwd=0.02, bench=0.02),
        _call(stance="neutral", fwd=0.05, bench=0.01),
        _call(stance=None, fwd=-0.02, bench=0.01),
    ]

    summary = ledger_mod.summarize(calls)

    assert summary.hit_rate["1m"] == pytest.approx(0.5)
    assert summary.n_directional["1m"] == 4
    assert summary.avg_excess["1m"] == pytest.approx((0.05 - 0.02 - 0.04 + 0.0 + 0.04 - 0.03) / 6)


def test_summarize_buckets_absent_when_empty_and_confidence_uses_1m_only():
    calls = [
        _call(stance="bullish", confidence="high", horizon="1m", fwd=0.03, bench=0.01),
        _call(stance="bearish", confidence="low", horizon="1w", fwd=0.03, bench=0.01),
    ]

    summary = ledger_mod.summarize(calls)

    assert "3m" not in summary.avg_excess
    assert summary.hit_rate_by_confidence == {"high": 1.0}


def test_record_call_idempotency(temp_db):
    result = _result()

    ledger_mod.record_call(result, "thorough")
    ledger_mod.record_call(result, "thorough")

    with db._conn() as c:
        rows = c.execute("SELECT symbol, as_of, mode, stance, confidence, price FROM calls").fetchall()
    assert len(rows) == 1
    assert dict(rows[0]) == {
        "symbol": "AAPL",
        "as_of": "2020-01-02",
        "mode": "thorough",
        "stance": "bullish",
        "confidence": "medium",
        "price": 100.0,
    }


async def test_evaluate_pending_inserts_matured_pairs_only_and_is_idempotent(
    temp_db, monkeypatch
):
    ledger_mod.record_call(_result(symbol="AAPL", fetched_at="2020-01-02T12:00:00Z"), "thorough")
    ledger_mod.record_call(_result(symbol="MSFT", fetched_at="2099-01-02T12:00:00Z"), "thorough")
    dates = pd.to_datetime(["2020-01-02", "2020-01-09", "2020-02-03", "2020-04-02"])
    close = pd.DataFrame(
        {
            "AAPL": [100.0, 105.0, 120.0, 130.0],
            "SPY": [100.0, 102.0, 110.0, 115.0],
        },
        index=dates,
    )
    calls = 0

    def fake_download(symbols, start):
        nonlocal calls
        calls += 1
        assert set(symbols) == {"AAPL", "SPY"}
        assert start == "2020-01-02"
        return close

    monkeypatch.setattr(ledger_mod, "_download_closes", fake_download)

    assert await ledger_mod.evaluate_pending() == 3
    assert await ledger_mod.evaluate_pending() == 0
    assert calls == 1

    calls_list = ledger_mod.list_calls()
    aapl = next(call for call in calls_list if call.symbol == "AAPL")
    msft = next(call for call in calls_list if call.symbol == "MSFT")
    assert set(aapl.outcomes) == {"1w", "1m", "3m"}
    assert msft.outcomes == {}
