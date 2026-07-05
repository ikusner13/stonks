from app.llm.critic import check_fabrication
from app.indicators.schemas import Indicator, IndicatorScorecard
from app.schemas import (
    Fundamentals,
    KeyMetric,
    NewsItem,
    Quote,
    Thesis,
    TickerData,
    TickerReport,
)

DATA = TickerData(
    symbol="AAPL",
    fetched_at="2026-06-21T00:00:00Z",
    quote=Quote(price=201.5, currency="USD", change=-1.23, change_percent=-0.61),
    fundamentals=Fundamentals(
        market_cap=3.05e12, pe_ratio=32.4, forward_pe=28.1, profit_margin=0.25, revenue=3.9e11
    ),
    news=[NewsItem(title="Apple ships 19.83 billion units", url="http://x", published_at="", source="wire")],
)


def scorecard(value: float = 0.1234) -> IndicatorScorecard:
    return IndicatorScorecard(
        symbol="AAPL",
        asof="2026-06-21T00:00:00Z",
        indicators=[
            Indicator(
                key="momentum_6m",
                label="6 month momentum",
                value=value,
                unit="pct",
                signal="bullish",
                detail=f"{value:+.1%} over 6 months",
            )
        ],
        bullish=1,
        bearish=0,
        neutral=0,
        unavailable=0,
        data_completeness=1.0,
    )


def report(**over) -> TickerReport:
    base = dict(
        symbol="AAPL",
        company_name="Apple Inc.",
        summary="A summary.",
        thesis=Thesis(bull=[], bear=[]),
        key_metrics=[],
        valuation_context="",
        risks=[],
        things_to_investigate=[],
        confidence="medium",
    )
    base.update(over)
    return TickerReport(**base)


def test_passes_when_every_figure_traces():
    r = report(
        key_metrics=[
            KeyMetric(label="P/E", value="32.4", interpretation=""),
            KeyMetric(label="Market cap", value="$3.05T", interpretation=""),
            KeyMetric(label="Price", value="201.5", interpretation=""),
        ],
        valuation_context="A forward P/E of 28.1 looks rich.",
    )
    assert check_fabrication(r, DATA, scorecard()).passed


def test_passes_with_no_numeric_claims():
    assert check_fabrication(report(), DATA, scorecard()).passed


def test_flags_keymetric_absent_from_ground_truth():
    r = report(key_metrics=[KeyMetric(label="PEG", value="1.87", interpretation="")])
    res = check_fabrication(r, DATA, scorecard())
    assert not res.passed
    assert "1.87" in res.details


def test_flags_fabricated_valuation_context():
    r = report(valuation_context="Trading at a wild 99.9 P/E.")
    res = check_fabrication(r, DATA, scorecard())
    assert not res.passed
    assert "99.9" in res.details


def test_grounds_percentage_against_stored_fraction():
    r = report(key_metrics=[KeyMetric(label="Margin", value="25%", interpretation="")])
    assert check_fabrication(r, DATA, scorecard()).passed


def test_grounds_spelled_out_magnitude_within_tolerance():
    r = report(key_metrics=[KeyMetric(label="Units", value="$19.8B", interpretation="")])
    assert check_fabrication(r, DATA, scorecard()).passed


def test_treats_news_headline_numbers_as_allowed():
    r = report(key_metrics=[KeyMetric(label="Units", value="19.83 billion", interpretation="")])
    assert check_fabrication(r, DATA, scorecard()).passed


def test_flags_fabricated_dollar_figure_in_prose():
    r = report(
        thesis=Thesis(
            bull=["Management says margins can support $9.9B in cash flow."],
            bear=[],
        )
    )
    res = check_fabrication(r, DATA, scorecard())
    assert not res.passed
    assert "thesis.bull[0]" in res.details
    assert "9900000000" in res.details


def test_prose_filters_common_non_financial_numbers():
    r = report(
        summary="The 2025 outlook references the latest 10-K.",
        thesis=Thesis(bull=["This remains a top 3 brand."], bear=[]),
    )
    assert check_fabrication(r, DATA, scorecard()).passed


def test_grounded_number_in_summary_passes():
    r = report(summary="Revenue is $390B and profit margin is 25%.")
    assert check_fabrication(r, DATA, scorecard()).passed


def test_scorecard_value_is_allowed_in_indicator_view():
    r = report(indicator_view="6 month momentum is 12.34%.")
    assert check_fabrication(r, DATA, scorecard()).passed


def test_prose_phrase_numbers_pass():
    r = report(
        summary=(
            "Trades near its 52-week high, above the 200-day average; 90d volatility "
            "is elevated and 12-1 month momentum is positive, echoing S&P 500 "
            "strength and 6M momentum."
        )
    )
    assert check_fabrication(r, DATA, scorecard()).passed


def test_flags_ungrounded_dollar_magnitude_in_prose():
    r = report(summary="$228B in buybacks would be material.")
    res = check_fabrication(r, DATA, scorecard())
    assert not res.passed
    assert "228000000000" in res.details


def test_decimal_magnitude_in_prose_matches_within_tolerance():
    r = report(summary="The segment reported 1.06B users.")
    data = DATA.model_copy(
        update={
            "news": [
                NewsItem(
                    title="Segment reports 1,058,482,000 users",
                    url="http://x",
                    published_at="",
                    source="wire",
                )
            ]
        }
    )
    assert check_fabrication(r, data, scorecard()).passed


def test_strict_field_still_flags_bare_integer():
    r = report(key_metrics=[KeyMetric(label="Range marker", value="52", interpretation="")])
    res = check_fabrication(r, DATA, scorecard()).passed
    assert not res


def test_scorecard_text_fields_are_allowed_in_prose():
    sc = IndicatorScorecard(
        symbol="AAPL",
        asof="2026-06-21T00:00:00Z",
        indicators=[
            Indicator(
                key="price_vs_200d",
                label="Price vs 200d trend",
                value=None,
                unit="pct",
                signal="bullish",
                detail="+23.4%",
            )
        ],
        bullish=1,
        bearish=0,
        neutral=0,
        unavailable=0,
        data_completeness=1.0,
    )
    r = report(summary="Price is +23.4% versus the 200d trend.")
    assert check_fabrication(r, DATA, sc).passed
