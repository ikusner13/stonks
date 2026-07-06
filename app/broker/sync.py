"""Broker sync orchestration."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from sqlite3 import IntegrityError

from pydantic import BaseModel

from .. import db
from ..portfolio.holdings import init_holdings_db, list_holdings
from ..portfolio.transactions import (
    _remove_holding_for_update,
    _set_cash_for_update,
    _upsert_holding_for_update,
    init_transactions_db,
    record_transaction_ledger_only,
)
from .reconcile import HoldingsDiff, diff_holdings, map_activities
from .snaptrade import fetch_activities, fetch_snapshot

LAST_BROKER_SYNC_KEY = "last_broker_sync"


class SyncResult(BaseModel):
    applied: bool
    diff: HoldingsDiff
    imported_activities: int
    skipped_activities: int
    warnings: list[str]
    asof: str


def _last_sync_since() -> date:
    raw = db.get_setting(LAST_BROKER_SYNC_KEY)
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC).date() - timedelta(days=90)


def _known_external_ids() -> set[str]:
    init_transactions_db()
    with db.connect() as c:
        rows = c.execute(
            "SELECT external_id FROM transactions WHERE external_id IS NOT NULL"
        ).fetchall()
    return {row["external_id"] for row in rows}


def _mirror_holdings(diff: HoldingsDiff) -> None:
    init_holdings_db()
    with db.connect() as c:
        for position in diff.to_upsert:
            _upsert_holding_for_update(c, position.symbol, position.shares, position.avg_cost)
        for symbol in diff.to_remove:
            _remove_holding_for_update(c, symbol)
        _set_cash_for_update(c, diff.cash_after)


async def run_sync(*, dry_run: bool = False, since: date | None = None) -> SyncResult:
    """``since`` overrides the incremental window — used to backfill history;
    external_id dedupe makes overlapping imports safe."""
    since = since or _last_sync_since()
    snapshot = await fetch_snapshot()
    activities = await fetch_activities(since)
    local_holdings = list_holdings()
    local_cash = db.get_cash()
    diff = diff_holdings(local_holdings, local_cash, snapshot)
    txns, skipped = map_activities(activities, _known_external_ids())
    warnings = [
        f"skipped activity {activity.external_id}: {activity.type}"
        for activity in skipped
    ]

    if dry_run:
        return SyncResult(
            applied=False,
            diff=diff,
            imported_activities=len(txns),
            skipped_activities=len(skipped),
            warnings=warnings,
            asof=snapshot.asof,
        )

    imported = 0
    for txn in txns:
        assert txn.external_id is not None
        try:
            record_transaction_ledger_only(txn, txn.external_id)
            imported += 1
        except IntegrityError:
            warnings.append(f"duplicate broker activity skipped: {txn.external_id}")

    _mirror_holdings(diff)
    db.set_setting(LAST_BROKER_SYNC_KEY, datetime.now(UTC).date().isoformat())
    return SyncResult(
        applied=True,
        diff=diff,
        imported_activities=imported,
        skipped_activities=len(skipped),
        warnings=warnings,
        asof=snapshot.asof,
    )

