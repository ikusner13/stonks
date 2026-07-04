import pytest

from app.portfolio.decision_support import suggest_position_size
from app.web.app import _effective_confidence


def test_effective_confidence_uses_more_conservative_grade():
    assert _effective_confidence("high", "medium") == "medium"
    assert _effective_confidence("low", "high") == "low"
    assert _effective_confidence("medium", "medium") == "medium"


def test_position_size_headroom_accounts_for_existing_weight():
    sizing = suggest_position_size(
        10_000,
        "medium",
        symbol="AAPL",
        current_weight=0.02,
    )

    assert sizing.low_pct == 0.03
    assert sizing.high_pct == 0.06
    assert sizing.low_dollars == pytest.approx(100)
    assert sizing.high_dollars == pytest.approx(400)
    assert sizing.current_weight == 0.02
    assert not sizing.already_at_band
    assert "remaining headroom" in sizing.note


def test_position_size_already_at_band_zeroes_headroom():
    sizing = suggest_position_size(
        10_000,
        "medium",
        symbol="AAPL",
        current_weight=0.08,
    )

    assert sizing.low_dollars == 0
    assert sizing.high_dollars == 0
    assert sizing.already_at_band
    assert "at/above" in sizing.note


def test_position_size_zero_portfolio_value():
    sizing = suggest_position_size(0, "high", symbol="AAPL")

    assert sizing.low_dollars == 0
    assert sizing.high_dollars == 0
    assert not sizing.already_at_band
    assert "Add holdings" in sizing.note
