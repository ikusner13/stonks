import pandas as pd

from app.portfolio.history import drop_short_history


def test_drop_short_history_keeps_long_window_and_excludes_short_symbol():
    index = pd.date_range("2026-01-01", periods=100)
    data = {f"L{i}": range(100) for i in range(5)}
    data["SHORT"] = [None] * 80 + list(range(20))
    close = pd.DataFrame(data, index=index)

    clean, excluded = drop_short_history(close)

    assert list(clean.columns) == ["L0", "L1", "L2", "L3", "L4"]
    assert excluded == ["SHORT"]
    assert len(clean) == 100


def test_drop_short_history_excludes_all_short_columns():
    index = pd.date_range("2026-01-01", periods=20)
    close = pd.DataFrame({"AAA": range(20), "BBB": range(20)}, index=index)

    clean, excluded = drop_short_history(close)

    assert clean.empty
    assert excluded == ["AAA", "BBB"]
