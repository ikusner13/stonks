import pytest

from app.portfolio.decision_support import suggest_position_size
from app.profiles.penny import PENNY
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


def test_penny_position_size_uses_profile_bands():
    sizing = suggest_position_size(100_000, "high", symbol="PENY", profile=PENNY, adv_dollars=1_000_000)

    assert sizing.profile == "penny"
    assert sizing.low_pct == 0.01
    assert sizing.high_pct == 0.03
    assert sizing.low_dollars == pytest.approx(1_000)
    assert sizing.high_dollars == pytest.approx(3_000)
    assert sizing.liquidity_cap_dollars == pytest.approx(300_000)
    assert "Liquidity cap applied" not in sizing.note


def test_penny_liquidity_cap_binds_value_note_and_field():
    sizing = suggest_position_size(1_000_000, "high", symbol="PENY", profile=PENNY, adv_dollars=50_000)

    assert sizing.low_dollars == pytest.approx(10_000)
    assert sizing.high_dollars == pytest.approx(15_000)
    assert sizing.liquidity_cap_dollars == pytest.approx(15_000)
    assert "exited in ~3 days at 10% of average daily dollar volume" in sizing.note


def test_penny_unknown_adv_warns_without_cap():
    sizing = suggest_position_size(1_000_000, "medium", symbol="PENY", profile=PENNY)

    assert sizing.low_dollars == pytest.approx(5_000)
    assert sizing.high_dollars == pytest.approx(15_000)
    assert sizing.liquidity_cap_dollars is None
    assert "Liquidity is unknown" in sizing.note


def test_largecap_position_size_defaults_unchanged():
    sizing = suggest_position_size(10_000, "low", symbol="AAPL")

    assert sizing.profile == "largecap"
    assert sizing.low_pct == 0.015
    assert sizing.high_pct == 0.03
    assert sizing.low_dollars == pytest.approx(150)
    assert sizing.high_dollars == pytest.approx(300)
    assert sizing.liquidity_cap_dollars is None
