from app.data.macro import MacroContext
from app.data.sec import SecFinancials
from app.indicators.confidence import clamp_confidence, compute_confidence
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.schemas import Fundamentals, NewsItem, Quote, TickerData


def _scorecard(values: int = 12) -> IndicatorScorecard:
    indicators = [
        Indicator(
            key=f"i{i}",
            label=f"Indicator {i}",
            value=float(i) if i < values else None,
            unit="ratio",
            signal="neutral" if i < values else "unavailable",
            detail="detail",
        )
        for i in range(12)
    ]
    return IndicatorScorecard(
        symbol="TST",
        asof="2026-07-04T00:00:00Z",
        indicators=indicators,
        bullish=0,
        bearish=0,
        neutral=values,
        unavailable=12 - values,
        data_completeness=values / 12,
    )


def _ticker(
    *,
    quote: bool = True,
    financials: bool = True,
    news_count: int = 3,
    macro: bool = True,
    sources: dict[str, str] | None = None,
) -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=10, currency="USD", change=0, change_percent=0) if quote else None,
        fundamentals=Fundamentals(market_cap=1_000, pe_ratio=10),
        financials=SecFinancials(net_income=100) if financials else None,
        news=[
            NewsItem(title=f"Story {i}", url=f"https://example.com/{i}", published_at="", source="wire")
            for i in range(news_count)
        ],
        macro=MacroContext(fed_funds_rate=5) if macro else None,
        sources=sources or {"quote": "ok", "financials": "ok"},
    )


def test_full_data_scores_high():
    assessment = compute_confidence(_ticker(), _scorecard())

    assert assessment.computed == "high"
    assert assessment.completeness == 1.0
    assert assessment.reasons


def test_missing_financials_with_source_error_caps_medium():
    data = _ticker(financials=False, sources={"financials": "error", "quote": "ok"})
    assessment = compute_confidence(data, _scorecard())

    assert assessment.computed == "medium"
    assert "financials: error" in assessment.reasons


def test_missing_quote_caps_low():
    assessment = compute_confidence(_ticker(quote=False), _scorecard())

    assert assessment.computed == "low"
    assert "quote missing: cap low" in assessment.reasons


def test_clamp_confidence_uses_lowest_grade():
    assert clamp_confidence("high", "medium", "high") == "medium"
    assert clamp_confidence("high", "low", "medium") == "low"
