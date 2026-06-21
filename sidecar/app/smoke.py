"""Offline smoke test: exercises the optimizer math on synthetic returns
(no network / yfinance). Run with `uv run python -m app.smoke`."""

import numpy as np
import pandas as pd
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio import RiskMeasure

from .optimize import _current_weights, _metrics
from .schemas import Holding


def main() -> None:
    rng = np.random.default_rng(42)
    cols = ["AAA", "BBB", "CCC"]
    # Synthetic daily returns with distinct drifts so weights are non-degenerate.
    data = rng.normal([0.0008, 0.0004, 0.0011], 0.015, size=(500, 3))
    returns = pd.DataFrame(data, columns=cols)

    model = MeanRisk(
        risk_measure=RiskMeasure.VARIANCE,
        objective_function=ObjectiveFunction.MAXIMIZE_RATIO,
    )
    model.fit(returns)
    w = np.asarray(model.weights_, dtype=float).ravel()
    assert w.shape == (3,), w.shape
    assert abs(w.sum() - 1.0) < 1e-6, w.sum()

    m = _metrics(w, returns, 0.0)
    assert m["volatility"] > 0 and np.isfinite(m["sharpe"])

    cur = _current_weights(
        [Holding(symbol="AAA", value=100), Holding(symbol="BBB", value=300)], cols
    )
    assert cur is not None and abs(sum(cur.values()) - 1.0) < 1e-6

    print("smoke ok:", {k: round(v, 4) for k, v in m.items()})
    print("optimal weights:", {c: round(float(x), 4) for c, x in zip(cols, w)})


if __name__ == "__main__":
    main()
