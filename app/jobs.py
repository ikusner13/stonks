"""In-process background jobs for portfolio maintenance and drift alerts."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta

import httpx

from . import config, db
from .portfolio.holdings import value_holdings
from .portfolio.plan import RebalancePlan, list_targets, plan_rebalance
from .portfolio.snapshots import record_snapshot

logger = logging.getLogger(__name__)

LAST_DRIFT_ALERT_KEY = "last_drift_alert"


def seconds_until_next(hour_utc: int, now: datetime) -> float:
    """Seconds until the next UTC occurrence of hour_utc."""
    now_utc = now.astimezone(UTC)
    target = datetime.combine(now_utc.date(), time(hour=hour_utc, tzinfo=UTC))
    if now_utc >= target:
        target += timedelta(days=1)
    return (target - now_utc).total_seconds()


def _symbol_set(value: str | None) -> set[str]:
    if not value or ":" not in value:
        return set()
    _, symbols = value.split(":", 1)
    return {symbol for symbol in symbols.split(",") if symbol}


def _money(value: float) -> str:
    rounded = round(abs(value), 2)
    if rounded.is_integer():
        return f"${rounded:,.0f}"
    return f"${rounded:,.2f}"


def _alert_message(plan: RebalancePlan) -> str:
    lines = []
    for item in plan.items:
        if item.action == "hold":
            continue
        shares = (
            "n/a"
            if item.delta_shares is None
            else f"{abs(item.delta_shares):,.4f}".rstrip("0").rstrip(".")
        )
        lines.append(
            "Rebalance drift: "
            f"{item.symbol} {item.current_weight * 100:.1f}% vs target "
            f"{item.target_weight * 100:.1f}% \u2192 {item.action} "
            f"{_money(item.delta_usd)} (~{shares} sh)"
        )
    lines.append(f"cash after: {_money(plan.cash_after)}")
    return "\n".join(lines)


async def run_daily_jobs() -> dict:
    """Record NAV and optionally send one deterministic drift alert."""
    try:
        valuation = await value_holdings()
        snapshot_recorded = record_snapshot(valuation)
    except Exception:
        logger.exception("daily valuation/snapshot job failed")
        return {"snapshot": False, "alert": ""}

    if not config.DRIFT_ALERT_ENABLED or not config.DISCORD_WEBHOOK_URL:
        return {"snapshot": snapshot_recorded, "alert": ""}

    plan = plan_rebalance(valuation, list_targets())
    actionable = [item for item in plan.items if item.action != "hold"] if plan else []
    if not actionable:
        return {"snapshot": snapshot_recorded, "alert": ""}

    current_symbols = {item.symbol for item in actionable}
    stored = db.get_setting(LAST_DRIFT_ALERT_KEY)
    if _symbol_set(stored) == current_symbols:
        return {"snapshot": snapshot_recorded, "alert": ""}

    today = datetime.now(UTC).date().isoformat()
    dedupe_value = f"{today}:{','.join(sorted(current_symbols))}"
    message = _alert_message(plan)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(config.DISCORD_WEBHOOK_URL, json={"content": message})
            response.raise_for_status()
    except Exception:
        logger.exception("daily drift alert failed")
        return {"snapshot": snapshot_recorded, "alert": ""}

    db.set_setting(LAST_DRIFT_ALERT_KEY, dedupe_value)
    return {"snapshot": snapshot_recorded, "alert": message}


async def daily_loop() -> None:
    """Sleep until the configured UTC hour and run daily jobs forever."""
    while True:
        await asyncio.sleep(seconds_until_next(config.DAILY_JOB_HOUR_UTC, datetime.now(UTC)))
        try:
            await run_daily_jobs()
        except Exception:
            logger.exception("daily job loop iteration failed")
