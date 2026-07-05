"""Single subprocess entry point for one model evaluation run."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Literal

from app.config import PREMIUM_MODEL, WORKHORSE_MODEL
from app.data import fetch_ticker_data
from app.indicators.confidence import compute_confidence
from app.indicators.engine import compute_scorecard
from app.llm.critic import research_ticker_reviewed
from app.llm.usage import with_run

Mode = Literal["cheap", "thorough"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one tilefish model evaluation.")
    parser.add_argument("--prewarm", action="store_true", help="Warm data caches only.")
    parser.add_argument("args", nargs="*")
    return parser


def _insufficient_data(ticker: object) -> bool:
    quote = getattr(ticker, "quote", None)
    fundamentals = getattr(ticker, "fundamentals", None)
    market_cap = getattr(fundamentals, "market_cap", None)
    return quote is None and market_cap is None


async def _prewarm(sym: str) -> int:
    ticker = await fetch_ticker_data(sym)
    if _insufficient_data(ticker):
        print(f"{sym}: no usable market data", file=sys.stderr)
        return 2
    await compute_scorecard(sym, ticker)
    print(f"{sym}: prewarmed data and scorecard", file=sys.stderr)
    return 0


async def _full_run(sym: str, mode: Mode, outpath: Path) -> int:
    async with with_run("eval", sym, mode) as ctx:
        ticker = await fetch_ticker_data(sym)
        scorecard = await compute_scorecard(sym, ticker)
        assessment = compute_confidence(ticker, scorecard)
        report, critique, revised = await research_ticker_reviewed(
            sym, ticker, scorecard, mode
        )

    result = {
        "ticker": sym,
        "mode": mode,
        "workhorse": WORKHORSE_MODEL,
        "premium": PREMIUM_MODEL,
        "report": report.model_dump(),
        "critique": critique.model_dump(),
        "revised": revised,
        "computed_confidence": assessment.computed,
        "usage": ctx.extra["_event"],
        "ground_truth": {
            "ticker_data": ticker.model_dump(),
            "scorecard": scorecard.model_dump(),
        },
    }
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(json.dumps(result, indent=2, default=str))
    return 0


async def main() -> int:
    parsed = _parser().parse_args()
    try:
        if parsed.prewarm:
            if len(parsed.args) != 1:
                raise ValueError("usage: eval_runner.py --prewarm TICKER")
            return await _prewarm(parsed.args[0].upper())

        if len(parsed.args) != 3:
            raise ValueError("usage: eval_runner.py TICKER MODE OUTPATH")
        sym = parsed.args[0].upper()
        mode = parsed.args[1]
        if mode not in ("cheap", "thorough"):
            raise ValueError("MODE must be 'cheap' or 'thorough'")
        return await _full_run(sym, mode, Path(parsed.args[2]))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
