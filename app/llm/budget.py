"""Daily LLM spend guard based on the usage JSONL log."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import config
from . import usage


class BudgetExceededError(RuntimeError):
    def __init__(self, spent: float, limit: float):
        self.spent = spent
        self.limit = limit
        super().__init__(f"daily LLM budget reached (${spent:.2f} of ${limit:.2f})")


def _cost_usd(event: dict[str, Any]) -> float:
    totals = event.get("totals")
    if not isinstance(totals, dict):
        return 0.0
    cost = totals.get("cost_usd", 0.0)
    return float(cost) if isinstance(cost, (int, float)) else 0.0


def spent_today() -> float:
    """Sum usage-event costs for today's UTC date."""
    today = datetime.now(UTC).date().isoformat()
    return sum(
        _cost_usd(event)
        for event in usage.read_events()
        if isinstance(event.get("ts"), str) and event["ts"].startswith(today)
    )


def check_budget() -> None:
    """Raise when the configured daily spend limit has already been reached."""
    limit = config.DAILY_LLM_BUDGET_USD
    if limit <= 0:
        return
    spent = spent_today()
    if spent >= limit:
        raise BudgetExceededError(spent, limit)
