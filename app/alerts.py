"""Shared alert helpers and deterministic alert jobs."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from . import config
from .data.sec_filings import (
    fetch_company_filings,
    fetch_ownership_filings,
    sec_client_session,
    ticker_cik_map,
)
from .db import connect, list_items
from .portfolio.holdings import init_holdings_db, list_holdings

logger = logging.getLogger(__name__)


def _init_alerts_db() -> None:
    with connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts_sent (
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (kind, key)
            )
            """
        )


def already_sent(kind: str, key: str) -> bool:
    """Return whether an alert has already been recorded as sent."""
    _init_alerts_db()
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM alerts_sent WHERE kind = ? AND key = ?",
            (kind, key),
        ).fetchone()
    return row is not None


def mark_sent(kind: str, key: str) -> None:
    """Record a successfully sent alert."""
    _init_alerts_db()
    with connect() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO alerts_sent (kind, key, sent_at)
            VALUES (?, ?, ?)
            """,
            (kind, key, datetime.now(UTC).isoformat()),
        )


def alert_universe() -> list[str]:
    """Symbols to monitor for alerts, combining watchlist and holdings."""
    init_holdings_db()
    symbols = {item.symbol.upper() for item in list_items()}
    symbols.update(holding.symbol.upper() for holding in list_holdings())
    return sorted(symbols)


async def run_sec_filing_alerts() -> dict:
    """Poll SEC EDGAR and send one batched Discord alert for new filings."""
    from .jobs import post_discord

    universe = alert_universe()
    try:
        cmap = await ticker_cik_map()
    except Exception:
        logger.exception("SEC ticker-CIK map fetch failed")
        return {"alerts": 0}

    since = datetime.now(UTC).date() - timedelta(days=config.SEC_LOOKBACK_DAYS)
    new_lines: list[tuple[str, str]] = []
    async with sec_client_session():
        for symbol in universe:
            cik = cmap.get(symbol)
            if cik is None:
                logger.debug("SEC ticker-CIK map missing %s", symbol)
                continue
            try:
                filings = await fetch_company_filings(symbol, cik, since)
                filings.extend(await fetch_ownership_filings(symbol, cik, since))
            except Exception:
                logger.exception("SEC filing fetch failed for %s", symbol)
                continue
            for filing in filings:
                if already_sent("sec_filing", filing.accession):
                    continue
                new_lines.append(
                    (
                        filing.accession,
                        f"SEC {filing.form} {filing.symbol} filed {filing.filing_date} {filing.url}",
                    )
                )

    if not new_lines:
        return {"alerts": 0}

    message = "\n".join(line for _, line in new_lines)
    try:
        await post_discord(message)
    except Exception:
        logger.exception("SEC filing Discord alert failed")
        return {"alerts": 0}

    for accession, _ in new_lines:
        mark_sent("sec_filing", accession)
    return {"alerts": len(new_lines)}
