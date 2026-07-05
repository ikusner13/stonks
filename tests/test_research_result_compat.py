from app.schemas import ResearchResult


def test_old_cached_research_result_json_still_validates():
    value = {
        "ticker": {
            "symbol": "AAPL",
            "fetched_at": "2026-07-04T00:00:00Z",
            "fundamentals": {},
            "news": [],
        },
        "report": {
            "symbol": "AAPL",
            "company_name": "Apple Inc.",
            "summary": "Old cached summary.",
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

    result = ResearchResult.model_validate(value)

    assert result.scorecard is None
    assert result.confidence_assessment is None
    assert result.report.indicator_view == ""
    assert result.profile == "largecap"
    assert result.profile_reason == ""
