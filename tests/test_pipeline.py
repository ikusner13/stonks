from app.indicators.confidence import ConfidenceAssessment
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.llm import pipeline
from app.schemas import (
    Critique,
    FabricationCheck,
    Fundamentals,
    Quote,
    Thesis,
    TickerData,
    TickerReport,
)


def _ticker() -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=10, currency="USD", change=0, change_percent=0),
        fundamentals=Fundamentals(market_cap=1_000_000_000, pe_ratio=10),
        sources={"quote": "ok"},
    )


def _scorecard(profile: str) -> IndicatorScorecard:
    return IndicatorScorecard(
        symbol="TST",
        asof="2026-07-04T00:00:00Z",
        profile=profile,
        indicators=[
            Indicator(
                key="momentum_6m",
                label="6 month momentum",
                value=0.1,
                unit="pct",
                signal="bullish",
                detail="detail",
            )
        ],
        bullish=1,
        bearish=0,
        neutral=0,
        unavailable=0,
        data_completeness=1.0,
    )


def _report(symbol: str) -> TickerReport:
    return TickerReport(
        symbol=symbol,
        company_name="Test Co.",
        summary="Summary.",
        thesis=Thesis(bull=[], bear=[]),
        key_metrics=[],
        valuation_context="",
        risks=[],
        things_to_investigate=[],
        confidence="high",
    )


def _critique() -> Critique:
    return Critique(
        fabrication_check=FabricationCheck(passed=True, details="ok"),
        issues=[],
        suggested_confidence="high",
        overall_assessment="ok",
    )


async def test_report_cache_key_includes_profile_and_result_metadata(monkeypatch):
    keys: list[str] = []

    async def fake_fetch_ticker_data(symbol, *, fresh=False):
        return _ticker()

    async def fake_compute_scorecard(symbol, data, *, profile, fresh=False):
        return _scorecard(profile.key)

    def fake_compute_confidence(data, scorecard, profile):
        return ConfidenceAssessment(computed="high", completeness=1.0, reasons=[])

    async def fake_research_ticker_reviewed(symbol, data, scorecard, profile, mode):
        return _report(symbol), _critique(), False

    async def fake_with_cache(namespace, key, ttl_ms, produce, *, fresh=False):
        keys.append(key)
        return await produce(), False

    monkeypatch.setattr(pipeline, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(pipeline, "compute_scorecard", fake_compute_scorecard)
    monkeypatch.setattr(pipeline, "compute_confidence", fake_compute_confidence)
    monkeypatch.setattr(pipeline, "research_ticker_reviewed", fake_research_ticker_reviewed)
    monkeypatch.setattr(pipeline, "with_cache", fake_with_cache)

    largecap = await pipeline.research_ticker_cached("tst", "cheap")
    penny = await pipeline.research_ticker_cached("tst", "cheap", profile_override="penny")

    assert keys[0].startswith("TST:")
    assert keys[0].endswith(":cheap:auto")
    assert keys[1].startswith("TST:")
    assert keys[1].endswith(":cheap:penny")
    assert keys[0] != keys[1]
    assert largecap.profile == "largecap"
    assert largecap.profile_reason == "default"
    assert penny.profile == "penny"
    assert penny.profile_reason == "manual override"


async def test_report_cache_hit_makes_no_network_fetch(monkeypatch):
    fetch_calls: list[str] = []

    async def fake_fetch_ticker_data(symbol, *, fresh=False):
        fetch_calls.append(symbol)
        return _ticker()

    cached = pipeline.ResearchResult(
        ticker=_ticker(),
        report=_report("TST"),
        critique=_critique(),
        revised=False,
    ).model_dump()

    async def fake_with_cache(namespace, key, ttl_ms, produce, *, fresh=False):
        return cached, True  # hit: produce() must never run

    monkeypatch.setattr(pipeline, "fetch_ticker_data", fake_fetch_ticker_data)
    monkeypatch.setattr(pipeline, "with_cache", fake_with_cache)

    result = await pipeline.research_ticker_cached("tst", "cheap")

    assert result.report.symbol == "TST"
    assert fetch_calls == []
