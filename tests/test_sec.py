import pandas as pd

from app.data.sec import _get_by_tags, _get_concept, _sanity_check


def test_exact_tag_lookup_prefers_real_total_assets_over_fuzzy_first_row():
    df = pd.DataFrame(
        [
            {
                "concept": "us-gaap_DebtSecuritiesHeldToMaturityX",
                "standard_concept": "Assets",
                "dimension": "",
                "2025-12-31": 2.7e11,
            },
            {
                "concept": "us-gaap_Assets",
                "standard_concept": "Assets",
                "dimension": "",
                "2025-12-31": 4.42e12,
            },
        ]
    )

    assert _get_by_tags(df, ["us-gaap_Assets"]) == 4.42e12


def test_revenue_preference_accepts_bank_revenue_tag():
    df = pd.DataFrame(
        [
            {
                "concept": "us-gaap_RevenuesNetOfInterestExpense",
                "standard_concept": "Revenue",
                "dimension": "",
                "2025-12-31": 182.4e9,
            }
        ]
    )

    assert _get_by_tags(df, ["us-gaap_RevenuesNetOfInterestExpense"]) == 182.4e9


def test_revenue_preference_uses_first_matching_tag():
    df = pd.DataFrame(
        [
            {
                "concept": "us-gaap_RevenuesNetOfInterestExpense",
                "standard_concept": "Revenue",
                "dimension": "",
                "2025-12-31": 182.4e9,
            },
            {
                "concept": "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "standard_concept": "Revenue",
                "dimension": "",
                "2025-12-31": 10.0,
            },
        ]
    )

    assert (
        _get_by_tags(
            df,
            [
                "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap_RevenuesNetOfInterestExpense",
            ],
        )
        == 10.0
    )


def test_newest_period_column_wins_for_exact_tags_and_standard_concept():
    df = pd.DataFrame(
        [
            {
                "concept": "us-gaap_Revenues",
                "standard_concept": "Revenue",
                "dimension": "",
                "2024-12-31": 100.0,
                "2025-12-31": 200.0,
            }
        ]
    )

    assert _get_by_tags(df, ["us-gaap_Revenues"]) == 200.0
    assert _get_concept(df, "Revenue") == 200.0


def test_sanity_check_drops_assets_and_liabilities_when_assets_below_liabilities():
    result = _sanity_check({"total_assets": 2.7e11, "total_liabilities": 4.06e12})

    assert result["total_assets"] is None
    assert result["total_liabilities"] is None


def test_sanity_check_keeps_consistent_values_unchanged():
    values = {
        "total_assets": 5.0,
        "total_liabilities": 4.0,
        "revenue": 2.0,
        "net_income": 1.0,
    }

    assert _sanity_check(values) == values


def test_sanity_check_drops_positive_net_income_above_positive_revenue():
    result = _sanity_check({"revenue": 1.0, "net_income": 2.0})

    assert result["revenue"] is None
    assert result["net_income"] is None


def test_sanity_check_keeps_negative_net_income_with_smaller_revenue():
    values = {"revenue": 1.0, "net_income": -2.0}

    assert _sanity_check(values) == values
