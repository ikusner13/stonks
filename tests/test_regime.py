import pandas as pd

from app.portfolio.decision_support import analyze_regime


def _alternating_returns(first_value: float, tail_value: float, rows: int = 140) -> pd.Series:
    values = []
    for i in range(rows):
        amplitude = tail_value if i >= rows - 21 else first_value
        values.append(amplitude if i % 2 == 0 else -amplitude)
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=rows, freq="B"))


def test_analyze_regime_elevated_when_recent_volatility_jumps():
    signal = analyze_regime(_alternating_returns(0.01, 0.04))

    assert signal is not None
    assert signal.level == "elevated"
    assert signal.vol_ratio >= 1.5
    assert "own longer-term norm" in signal.note


def test_analyze_regime_normal_when_recent_volatility_matches_history():
    signal = analyze_regime(_alternating_returns(0.01, 0.01))

    assert signal is not None
    assert signal.level == "normal"
    assert 0.75 < signal.vol_ratio < 1.5


def test_analyze_regime_calm_when_recent_volatility_quiets():
    signal = analyze_regime(_alternating_returns(0.02, 0.005))

    assert signal is not None
    assert signal.level == "calm"
    assert signal.vol_ratio <= 0.75


def test_analyze_regime_none_with_short_sample():
    returns = pd.Series([0.01, -0.01] * 25)

    assert analyze_regime(returns) is None
