"""Thin read-only SnapTrade client adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel

from .. import config

logger = logging.getLogger(__name__)


class BrokerPosition(BaseModel):
    symbol: str
    shares: float
    avg_cost: float | None


class BrokerActivity(BaseModel):
    external_id: str
    ts: str
    type: str
    symbol: str | None
    shares: float | None
    price: float | None
    amount: float
    description: str = ""


class BrokerSnapshot(BaseModel):
    account_id: str
    positions: list[BrokerPosition]
    cash: float
    asof: str


def _configured() -> bool:
    return bool(config.SNAPTRADE_CLIENT_ID and config.SNAPTRADE_CONSUMER_KEY)


def _require_configured() -> None:
    if not _configured():
        raise RuntimeError("SnapTrade is not configured")


def _require_user_credentials() -> tuple[str, str]:
    _require_configured()
    if not config.SNAPTRADE_USER_ID:
        raise RuntimeError("SNAPTRADE_USER_ID is required")
    if not config.SNAPTRADE_USER_SECRET:
        raise RuntimeError("SNAPTRADE_USER_SECRET is required")
    return config.SNAPTRADE_USER_ID, config.SNAPTRADE_USER_SECRET


def _client():
    # Keep the generated SDK import confined to this adapter.
    from snaptrade_client import SnapTrade

    return SnapTrade(
        client_id=config.SNAPTRADE_CLIENT_ID,
        consumer_key=config.SNAPTRADE_CONSUMER_KEY,
    )


def _body(response: Any) -> Any:
    return getattr(response, "body", response)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_list(value: Any) -> list[Any]:
    value = _body(value)
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        current = _get(current, key)
        if current is None:
            return None
    return current


def _position_symbol(row: Any) -> str | None:
    instrument = _get(row, "instrument")
    symbol = _get(instrument, "symbol") or _get(instrument, "raw_symbol")
    if symbol:
        return str(symbol).strip().upper()

    legacy_symbol = _get(row, "symbol")
    symbol = _nested(legacy_symbol, "symbol", "symbol") or _nested(
        legacy_symbol, "symbol", "raw_symbol"
    )
    return str(symbol).strip().upper() if symbol else None


def _activity_symbol(row: Any) -> str | None:
    symbol = _get(row, "symbol")
    raw = _get(symbol, "symbol") or _get(symbol, "raw_symbol")
    return str(raw).strip().upper() if raw else None


def _iso_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    return None


def _normalize_position(row: Any) -> BrokerPosition | None:
    if bool(_get(row, "cash_equivalent", False)):
        return None

    symbol = _position_symbol(row)
    shares = _to_float(_get(row, "units") or _get(row, "fractional_units"))
    if not symbol or shares is None or shares <= 0:
        logger.warning("skipping SnapTrade position with missing symbol/shares: %r", row)
        return None

    avg_cost = _to_float(_get(row, "cost_basis") or _get(row, "average_purchase_price"))
    return BrokerPosition(symbol=symbol, shares=shares, avg_cost=avg_cost)


def _normalize_activity(row: Any) -> BrokerActivity | None:
    external_id = str(_get(row, "id") or "").strip()
    raw_type = str(_get(row, "type") or "").strip().upper()
    ts = _iso_date(_get(row, "trade_date") or _get(row, "settlement_date"))
    amount = _to_float(_get(row, "amount"))
    shares = _to_float(_get(row, "units"))
    price = _to_float(_get(row, "price"))

    if not external_id or not raw_type or not ts:
        logger.warning("skipping SnapTrade activity with missing id/type/date: %r", row)
        return None
    if amount is None:
        if shares is None or price is None:
            logger.warning("skipping SnapTrade activity with missing amount: %r", row)
            return None
        amount = shares * price

    return BrokerActivity(
        external_id=external_id,
        ts=ts,
        type=raw_type,
        symbol=_activity_symbol(row),
        shares=abs(shares) if shares is not None else None,
        price=abs(price) if price is not None else None,
        amount=abs(amount),
        description=str(_get(row, "description") or ""),
    )


def _account_label(account: Any) -> str:
    account_id = str(_get(account, "id") or "")
    name = str(_get(account, "name") or "")
    institution = str(_get(account, "institution_name") or "")
    bits = [bit for bit in (account_id, name, institution) if bit]
    return " / ".join(bits) or repr(account)


def _select_account_id(accounts: list[Any]) -> str:
    if config.SNAPTRADE_ACCOUNT_ID:
        wanted = config.SNAPTRADE_ACCOUNT_ID
        if any(str(_get(account, "id")) == wanted for account in accounts):
            return wanted
        listing = "; ".join(_account_label(account) for account in accounts)
        raise RuntimeError(f"SNAPTRADE_ACCOUNT_ID={wanted} not found; accounts: {listing}")

    if len(accounts) == 1:
        account_id = str(_get(accounts[0], "id") or "")
        if account_id:
            return account_id

    listing = "; ".join(_account_label(account) for account in accounts)
    raise RuntimeError(f"set SNAPTRADE_ACCOUNT_ID; available accounts: {listing}")


def _fetch_accounts_sync() -> tuple[Any, str, str, list[Any]]:
    user_id, user_secret = _require_user_credentials()
    client = _client()
    accounts = _as_list(
        client.account_information.list_user_accounts(
            user_id=user_id,
            user_secret=user_secret,
        )
    )
    if not accounts:
        raise RuntimeError("SnapTrade returned no linked accounts")
    return client, user_id, user_secret, accounts


def _fetch_snapshot_sync() -> BrokerSnapshot:
    client, user_id, user_secret, accounts = _fetch_accounts_sync()
    account_id = _select_account_id(accounts)

    raw_positions = _body(
        client.account_information.get_all_account_positions(
            user_id=user_id,
            user_secret=user_secret,
            account_id=account_id,
        )
    )
    position_rows = _get(raw_positions, "results", raw_positions)
    if not isinstance(position_rows, list):
        position_rows = []
    positions = [
        normalized
        for row in position_rows
        if (normalized := _normalize_position(row)) is not None
    ]

    balances = _as_list(
        client.account_information.get_user_account_balance(
            user_id=user_id,
            user_secret=user_secret,
            account_id=account_id,
        )
    )
    cash = 0.0
    for balance in balances:
        currency = _get(_get(balance, "currency"), "code") or _get(balance, "currency")
        if currency and str(currency).upper() != "USD":
            logger.warning("skipping non-USD SnapTrade cash balance: %r", balance)
            continue
        cash_value = _to_float(_get(balance, "cash"))
        if cash_value is not None:
            cash += cash_value

    return BrokerSnapshot(
        account_id=account_id,
        positions=positions,
        cash=max(0.0, cash),
        asof=datetime.now(UTC).isoformat(),
    )


def _fetch_activities_sync(since: date) -> list[BrokerActivity]:
    client, user_id, user_secret, accounts = _fetch_accounts_sync()
    account_id = _select_account_id(accounts)
    end_date = datetime.now(UTC).date()
    activities: list[BrokerActivity] = []
    offset = 0
    limit = 1000

    while True:
        body = _body(
            client.account_information.get_account_activities(
                account_id=account_id,
                user_id=user_id,
                user_secret=user_secret,
                start_date=since,
                end_date=end_date,
                offset=offset,
                limit=limit,
            )
        )
        rows = _get(body, "data", body)
        if not isinstance(rows, list):
            rows = []
        activities.extend(
            normalized
            for row in rows
            if (normalized := _normalize_activity(row)) is not None
        )
        if len(rows) < limit:
            break
        offset += limit
    return activities


def _register_user_sync() -> str:
    _require_configured()
    if not config.SNAPTRADE_USER_ID:
        raise RuntimeError("SNAPTRADE_USER_ID is required")
    response = _client().authentication.register_snap_trade_user(
        user_id=config.SNAPTRADE_USER_ID
    )
    user_secret = _get(_body(response), "userSecret")
    if not user_secret:
        raise RuntimeError("SnapTrade register response did not include userSecret")
    return str(user_secret)


def _connection_portal_url_sync() -> str:
    user_id, user_secret = _require_user_credentials()
    response = _client().authentication.login_snap_trade_user(
        user_id=user_id,
        user_secret=user_secret,
        connection_type="read",
        show_close_button=True,
    )
    body = _body(response)
    if isinstance(body, str):
        return body
    url = _get(body, "redirectURI") or _get(body, "redirect_uri") or _get(body, "url")
    if not url:
        raise RuntimeError("SnapTrade login response did not include a portal URL")
    return str(url)


async def fetch_snapshot() -> BrokerSnapshot:
    return await asyncio.to_thread(_fetch_snapshot_sync)


async def fetch_activities(since: date) -> list[BrokerActivity]:
    return await asyncio.to_thread(_fetch_activities_sync, since)


async def register_user() -> str:
    return await asyncio.to_thread(_register_user_sync)


async def connection_portal_url() -> str:
    return await asyncio.to_thread(_connection_portal_url_sync)
