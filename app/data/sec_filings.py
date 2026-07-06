"""SEC EDGAR filing-alert data access."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from .. import config
from ..cache import with_cache

logger = logging.getLogger(__name__)

USER_AGENT = f"stonks {os.getenv('SEC_IDENTITY', 'ikusner13@gmail.com')}"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_SEC_TICKERS_TTL_MS = 24 * 60 * 60 * 1000
_OWNERSHIP_FORMS = "SC 13D,SC 13D/A,SC 13G,SC 13G/A"
_REQUEST_SPACING_SECONDS = 0.25
_request_lock = asyncio.Lock()
_last_request_at = 0.0
_current_client: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "sec_filings_client",
    default=None,
)


class NewFiling(BaseModel):
    symbol: str
    cik: int
    form: str
    filing_date: str
    accession: str
    url: str


@contextlib.asynccontextmanager
async def sec_client_session() -> AsyncIterator[httpx.AsyncClient]:
    """Share one SEC HTTP client across a filing-alert job run."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        token = _current_client.set(client)
        try:
            yield client
        finally:
            _current_client.reset(token)


async def _request_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> Any:
    global _last_request_at
    async with _request_lock:
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - _last_request_at
        if elapsed < _REQUEST_SPACING_SECONDS:
            await asyncio.sleep(_REQUEST_SPACING_SECONDS - elapsed)
        active_client = client or _current_client.get()
        if active_client is None:
            async with httpx.AsyncClient(headers=HEADERS, timeout=15) as fallback:
                response = await fallback.get(url, params=params)
        else:
            response = await active_client.get(url, params=params)
        _last_request_at = loop.time()
    response.raise_for_status()
    return response.json()


def _filing_url(cik: int, accession: str, primary_document: str | None = None) -> str:
    accession_path = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_path}"
    if primary_document:
        return f"{base}/{primary_document}"
    return base


def _accession_filer_cik(accession: str, fallback_cik: int) -> int:
    """The first accession segment is the filer CIK that owns the archive path."""
    try:
        return int(accession.split("-", 1)[0])
    except ValueError:
        return fallback_cik


async def ticker_cik_map() -> dict[str, int]:
    """Fetch SEC's ticker-to-CIK map, cached for 24 hours."""

    async def produce() -> dict[str, int]:
        data = await _request_json("https://www.sec.gov/files/company_tickers.json")
        if not isinstance(data, dict):
            raise ValueError("unexpected SEC ticker map shape")
        out: dict[str, int] = {}
        for item in data.values():
            if not isinstance(item, dict):
                continue
            ticker = item.get("ticker")
            cik = item.get("cik_str")
            if ticker is None or cik is None:
                continue
            out[str(ticker).upper()] = int(cik)
        if not out:
            raise ValueError("empty SEC ticker map")
        return out

    value, _ = await with_cache("sec_tickers", "all", _SEC_TICKERS_TTL_MS, produce)
    return {str(symbol).upper(): int(cik) for symbol, cik in value.items()}


async def fetch_company_filings(symbol: str, cik: int, since: date) -> list[NewFiling]:
    """Fetch 8-K/10-Q/10-K-style filings from a company's submissions JSON."""
    data = await _request_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    recent = data.get("filings", {}).get("recent", {}) if isinstance(data, dict) else {}
    accessions = recent.get("accessionNumber") or []
    filing_dates = recent.get("filingDate") or []
    forms = recent.get("form") or []
    primary_documents = recent.get("primaryDocument") or []

    out: list[NewFiling] = []
    for accession, filing_date, form, primary_document in zip(
        accessions,
        filing_dates,
        forms,
        primary_documents,
    ):
        try:
            parsed_date = date.fromisoformat(str(filing_date))
        except ValueError:
            continue
        if parsed_date < since:
            break
        if form not in config.SEC_ALERT_FORMS:
            continue
        accession_str = str(accession)
        out.append(
            NewFiling(
                symbol=symbol.upper(),
                cik=cik,
                form=str(form),
                filing_date=parsed_date.isoformat(),
                accession=accession_str,
                url=_filing_url(cik, accession_str, str(primary_document or "")),
            )
        )
    return out


async def fetch_ownership_filings(symbol: str, cik: int, since: date) -> list[NewFiling]:
    """Fetch SC 13D/13G ownership filings via SEC EFTS full-text search."""
    params = {
        "q": '""',
        "forms": _OWNERSHIP_FORMS,
        "ciks": f"{cik:010d}",
        "startdt": since.isoformat(),
        "enddt": datetime.now(UTC).date().isoformat(),
    }
    try:
        data = await _request_json("https://efts.sec.gov/LATEST/search-index", params=params)
    except Exception:
        logger.warning("SEC EFTS ownership filing fetch failed for %s", symbol, exc_info=True)
        return []

    hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
    if not isinstance(hits, list):
        return []

    out: list[NewFiling] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source")
        if not isinstance(source, dict):
            continue
        filing_date = source.get("file_date")
        form = source.get("file_type")
        hit_id = hit.get("_id")
        if not filing_date or not form or not hit_id:
            continue
        try:
            parsed_date = date.fromisoformat(str(filing_date))
        except ValueError:
            continue
        parts = str(hit_id).split(":", 1)
        accession = parts[0]
        primary_document = parts[1] if len(parts) == 2 else ""
        if not accession:
            continue
        out.append(
            NewFiling(
                symbol=symbol.upper(),
                cik=cik,
                form=str(form),
                filing_date=parsed_date.isoformat(),
                accession=accession,
                url=_filing_url(_accession_filer_cik(accession, cik), accession, primary_document),
            )
        )
    return out
