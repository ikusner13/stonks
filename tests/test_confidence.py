import pytest

from app.data.macro import MacroContext
from app.data.sec import SecFinancials
from app.indicators.confidence import clamp_confidence, compute_confidence
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.profiles.largecap import LARGECAP
from app.profiles.penny import PENNY
from app.schemas import Fundamentals, NewsItem, Quote, TickerData


def _scorecard(values: int = 12, *, total: int = 12) -> IndicatorScorecard:
    indicators = [
        Indicator(
            key=f"i{i}",
            label=f"Indicator {i}",
            value=float(i) if i < values else None,
            unit="ratio",
            signal="neutral" if i < values else "unavailable",
            detail="detail",
        )
        for i in range(total)
    ]
    return IndicatorScorecard(
        symbol="TST",
        asof="2026-07-04T00:00:00Z",
        indicators=indicators,
        bullish=0,
        bearish=0,
        neutral=values,
        unavailable=total - values,
        data_completeness=values / total,
    )


def _ticker(
    *,
    quote: bool = True,
    financials: bool = True,
    news_count: int = 3,
    macro: bool = True,
    fundamentals: Fundamentals | None = None,
    sources: dict[str, str] | None = None,
) -> TickerData:
    return TickerData(
        symbol="TST",
        fetched_at="2026-07-04T00:00:00Z",
        quote=Quote(price=10, currency="USD", change=0, change_percent=0) if quote else None,
        fundamentals=fundamentals or Fundamentals(market_cap=1_000, pe_ratio=10),
        financials=SecFinancials(net_income=100) if financials else None,
        news=[
            NewsItem(title=f"Story {i}", url=f"https://example.com/{i}", published_at="", source="wire")
            for i in range(news_count)
        ],
        macro=MacroContext(fed_funds_rate=5) if macro else None,
        sources=sources or {"quote": "ok", "financials": "ok"},
    )


def test_full_data_scores_high():
    assessment = compute_confidence(_ticker(), _scorecard(), LARGECAP)

    assert assessment.computed == "high"
    assert assessment.completeness == 1.0
    assert assessment.reasons


def test_missing_financials_with_source_error_caps_medium():
    data = _ticker(financials=False, sources={"financials": "error", "quote": "ok"})
    assessment = compute_confidence(data, _scorecard(), LARGECAP)

    assert assessment.computed == "medium"
    assert "financials: error" in assessment.reasons


def test_missing_quote_caps_low():
    assessment = compute_confidence(_ticker(quote=False), _scorecard(), LARGECAP)

    assert assessment.computed == "low"
    assert "quote missing: cap low" in assessment.reasons


def test_clamp_confidence_uses_lowest_grade():
    assert clamp_confidence("high", "medium", "high") == "medium"
    assert clamp_confidence("high", "low", "medium") == "low"


def test_penny_confidence_uses_profile_weights_and_omits_zero_weight_reasons():
    weights = PENNY.confidence_weights
    assert (
        weights.quote
        + weights.fundamentals
        + weights.financials
        + weights.news
        + weights.macro
        + weights.scorecard
    ) == pytest.approx(1.0)

    assessment = compute_confidence(_ticker(news_count=10, macro=True), _scorecard(15, total=15), PENNY)

    assert assessment.computed == "high"
    assert assessment.completeness == 1.0
    assert not any(reason.startswith("news ") for reason in assessment.reasons)
    assert "macro present" not in assessment.reasons


def test_penny_news_count_does_not_move_completeness():
    with_news = compute_confidence(_ticker(news_count=10), _scorecard(15, total=15), PENNY)
    without_news = compute_confidence(_ticker(news_count=0), _scorecard(15, total=15), PENNY)

    assert with_news.completeness == without_news.completeness


def test_new_fundamental_fields_do_not_count_toward_presence():
    data = _ticker(
        fundamentals=Fundamentals(exchange="OQB", float_shares=1_000_000, shares_outstanding=2_000_000)
    )
    assessment = compute_confidence(data, _scorecard(15, total=15), PENNY)

    assert "fundamentals 0/5 fields" in assessment.reasons
    assert assessment.completeness == 0.9


def test_dark_company_cap_only_for_penny_when_financials_missing():
    data = _ticker(financials=False)
    penny = compute_confidence(data, _scorecard(15, total=15), PENNY)
    largecap = compute_confidence(data, _scorecard(), LARGECAP)

    assert penny.computed == "low"
    assert "no SEC financials: dark-company cap low" in penny.reasons
    assert largecap.computed == "high"
    assert "no SEC financials: dark-company cap low" not in largecap.reasons


def test_dark_company_cap_does_not_fire_when_financials_object_exists():
    data = _ticker(financials=True)
    assessment = compute_confidence(data, _scorecard(15, total=15), PENNY)

    assert assessment.computed == "high"
    assert "no SEC financials: dark-company cap low" not in assessment.reasons
