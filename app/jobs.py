"""In-process background jobs for portfolio maintenance and drift alerts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from . import config, db
from .portfolio.holdings import value_holdings
from .portfolio.plan import RebalancePlan, list_targets, plan_rebalance
from .portfolio.snapshots import record_snapshot

logger = logging.getLogger(__name__)

LAST_DRIFT_ALERT_KEY = "last_drift_alert"
LAST_RUN_PREFIX = "last_run:"


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[], Awaitable[object]]
    at_hour_utc: int | None = None
    cadence: timedelta | None = None

    def __post_init__(self) -> None:
        if (self.at_hour_utc is None) == (self.cadence is None):
            raise ValueError("exactly one of at_hour_utc or cadence must be set")


def is_due(job: Job, now: datetime, last_run: datetime | None) -> bool:
    """Return whether a job should run at this UTC tick."""
    now_utc = now.astimezone(UTC)
    last_run_utc = last_run.astimezone(UTC) if last_run is not None else None
    if job.at_hour_utc is not None:
        return now_utc.hour >= job.at_hour_utc and (
            last_run_utc is None or last_run_utc.date() < now_utc.date()
        )
    assert job.cadence is not None
    return last_run_utc is None or now_utc - last_run_utc >= job.cadence


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


async def post_discord(message: str) -> None:
    """Post a Discord webhook message; caller decides what failure means."""
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(config.DISCORD_WEBHOOK_URL, json={"content": message})
        response.raise_for_status()


async def run_daily_jobs() -> dict:
    """Record NAV and optionally send one deterministic drift alert."""
    try:
        valuation = await value_holdings()
        snapshot_recorded = record_snapshot(valuation)
    except Exception:
        logger.exception("daily valuation/snapshot job failed")
        return {"snapshot": False, "alert": ""}

    try:
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

        await post_discord(message)
        db.set_setting(LAST_DRIFT_ALERT_KEY, dedupe_value)
        return {"snapshot": snapshot_recorded, "alert": message}
    except Exception:
        logger.exception("daily drift alert failed")
        return {"snapshot": snapshot_recorded, "alert": ""}


def _last_run_key(job: Job) -> str:
    return f"{LAST_RUN_PREFIX}{job.name}"


def _parse_last_run(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def run_due_jobs(jobs: list[Job], now: datetime | None = None) -> dict[str, bool]:
    """Run due jobs and advance only successful last-run ledger entries."""
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    results: dict[str, bool] = {}
    for job in jobs:
        last_run = _parse_last_run(db.get_setting(_last_run_key(job)))
        if not is_due(job, now_utc, last_run):
            continue
        try:
            await job.run()
        except Exception:
            logger.exception("job %s failed", job.name)
            results[job.name] = False
            continue
        db.set_setting(_last_run_key(job), now_utc.isoformat())
        results[job.name] = True
    return results


async def scheduler_loop(jobs: list[Job], tick_seconds: int | None = None) -> None:
    """Run due jobs on startup and then on each scheduler tick forever."""
    tick = config.SCHEDULER_TICK_SECONDS if tick_seconds is None else tick_seconds
    try:
        await run_due_jobs(jobs)
    except Exception:
        logger.exception("scheduler tick failed")
    while True:
        await asyncio.sleep(tick)
        try:
            await run_due_jobs(jobs)
        except Exception:
            logger.exception("scheduler tick failed")


def build_jobs() -> list[Job]:
    """Build the registered background jobs for this process."""
    registry: list[Job] = []
    if config.DAILY_JOB_HOUR_UTC >= 0:
        registry.append(
            Job(
                name="daily_portfolio",
                run=run_daily_jobs,
                at_hour_utc=config.DAILY_JOB_HOUR_UTC,
            )
        )
    if config.ALERTS_ENABLED and config.DISCORD_WEBHOOK_URL:
        from .alerts import run_earnings_alerts, run_price_alerts

        registry.extend(
            [
                Job(
                    name="price_alerts",
                    run=run_price_alerts,
                    at_hour_utc=config.ALERTS_HOUR_UTC,
                ),
                Job(
                    name="earnings_alerts",
                    run=run_earnings_alerts,
                    at_hour_utc=config.ALERTS_HOUR_UTC,
                ),
            ]
        )
    if config.SEC_ALERTS_ENABLED and config.DISCORD_WEBHOOK_URL:
        from .alerts import run_sec_filing_alerts

        registry.append(
            Job(
                name="sec_filing_alerts",
                run=run_sec_filing_alerts,
                cadence=timedelta(hours=config.SEC_ALERT_HOURS),
            )
        )
    return registry
