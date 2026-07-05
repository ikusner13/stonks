"""Transaction journal, apply semantics, and money-weighted return math."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite

from pydantic import BaseModel

from .. import db
from .holdings import Holding, PortfolioValuation

SIDES = ("buy", "sell", "deposit", "withdraw")
_ZERO_TOLERANCE = 1e-9
_CENT = Decimal("0.01")


class Transaction(BaseModel):
    id: int | None = None
    ts: str
    side: str
    symbol: str | None
    shares: float | None
    price: float | None
    amount: float
    realized_pl: float | None
    note: str = ""


class ReturnsSummary(BaseModel):
    mwr_annualized: float | None
    mwr_note: str
    total_deposited: float
    total_withdrawn: float
    realized_pl_total: float
    realized_pl_by_year: dict[str, float]
    first_flow_date: str | None
    flow_count: int


def _round_cents(value: float | Decimal) -> float:
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _parse_txn_date(raw: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("date must be an ISO date YYYY-MM-DD") from exc
    if raw != parsed.isoformat():
        raise ValueError("date must be an ISO date YYYY-MM-DD")
    if parsed > datetime.now(UTC).date():
        raise ValueError("date cannot be in the future")
    return parsed


def _positive_number(value: float | None, label: str) -> float:
    if value is None or value <= 0 or not isfinite(value):
        raise ValueError(f"{label} must be > 0")
    return value


def _get_holding_for_update(c, symbol: str) -> Holding | None:
    row = c.execute(
        "SELECT symbol, shares, avg_cost FROM holdings WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return Holding(symbol=row["symbol"], shares=row["shares"], avg_cost=row["avg_cost"])


def _get_cash_for_update(c) -> float:
    row = c.execute("SELECT value FROM settings WHERE key = 'cash'").fetchone()
    if row is None:
        return 0.0
    try:
        value = float(row["value"])
    except (TypeError, ValueError):
        return 0.0
    return value if value >= 0 and isfinite(value) else 0.0


def _set_cash_for_update(c, amount: float) -> None:
    c.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('cash', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(amount),),
    )


def _upsert_holding_for_update(
    c,
    symbol: str,
    shares: float,
    avg_cost: float | None,
) -> None:
    c.execute(
        """
        INSERT INTO holdings (symbol, shares, avg_cost, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(symbol) DO UPDATE SET
            shares = excluded.shares,
            avg_cost = excluded.avg_cost,
            updated_at = excluded.updated_at
        """,
        (symbol, shares, avg_cost),
    )


def _remove_holding_for_update(c, symbol: str) -> None:
    c.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))


def init_transactions_db() -> None:
    """Create the transactions table if it doesn't exist yet; idempotent."""
    with db.connect() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                side TEXT NOT NULL,
                symbol TEXT,
                shares REAL,
                price REAL,
                amount REAL NOT NULL,
                realized_pl REAL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def _validated_transaction(txn: Transaction) -> Transaction:
    _parse_txn_date(txn.ts)
    side = txn.side.strip().lower()
    if side not in SIDES:
        raise ValueError(f"side must be one of {', '.join(SIDES)}")

    note = txn.note or ""
    if side in {"buy", "sell"}:
        symbol = (txn.symbol or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required for buy/sell")
        shares = _positive_number(txn.shares, "shares")
        price = _positive_number(txn.price, "price")
        amount = _round_cents(Decimal(str(shares)) * Decimal(str(price)))
        return Transaction(
            id=txn.id,
            ts=txn.ts,
            side=side,
            symbol=symbol,
            shares=shares,
            price=price,
            amount=amount,
            realized_pl=txn.realized_pl,
            note=note,
        )

    amount = _positive_number(txn.amount, "amount")
    return Transaction(
        id=txn.id,
        ts=txn.ts,
        side=side,
        symbol=None,
        shares=None,
        price=None,
        amount=_round_cents(amount),
        realized_pl=None,
        note=note,
    )


def apply_transaction(txn: Transaction) -> Transaction:
    """Validate, mutate holdings/cash, insert the row, and return the saved transaction."""
    init_transactions_db()
    clean = _validated_transaction(txn)
    realized_pl: float | None = None

    with db.connect() as c:
        cash = _get_cash_for_update(c)
        holding = _get_holding_for_update(c, clean.symbol) if clean.symbol else None

        if clean.side == "buy":
            cash_after = cash - clean.amount
            if cash_after < 0:
                raise ValueError("insufficient cash — record a deposit first or adjust cash")
            if holding is None:
                new_shares = clean.shares or 0.0
                new_avg = clean.price
            else:
                new_shares = holding.shares + (clean.shares or 0.0)
                if holding.avg_cost is None:
                    new_avg = None
                else:
                    new_avg = (
                        (holding.shares * holding.avg_cost)
                        + ((clean.shares or 0.0) * (clean.price or 0.0))
                    ) / new_shares
            _set_cash_for_update(c, cash_after)
            _upsert_holding_for_update(c, clean.symbol or "", new_shares, new_avg)

        elif clean.side == "sell":
            if holding is None or (clean.shares or 0.0) > holding.shares + _ZERO_TOLERANCE:
                raise ValueError("sell shares exceed held shares")
            if holding.avg_cost is not None:
                realized_pl = _round_cents(((clean.price or 0.0) - holding.avg_cost) * (clean.shares or 0.0))
            new_shares = holding.shares - (clean.shares or 0.0)
            if new_shares < _ZERO_TOLERANCE:
                _remove_holding_for_update(c, clean.symbol or "")
            else:
                _upsert_holding_for_update(c, clean.symbol or "", new_shares, holding.avg_cost)
            _set_cash_for_update(c, cash + clean.amount)

        elif clean.side == "deposit":
            _set_cash_for_update(c, cash + clean.amount)

        elif clean.side == "withdraw":
            cash_after = cash - clean.amount
            if cash_after < 0:
                raise ValueError("withdrawal exceeds cash")
            _set_cash_for_update(c, cash_after)

        row = c.execute(
            """
            INSERT INTO transactions (ts, side, symbol, shares, price, amount, realized_pl, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                clean.ts,
                clean.side,
                clean.symbol,
                clean.shares,
                clean.price,
                clean.amount,
                realized_pl,
                clean.note,
            ),
        ).fetchone()

    return Transaction(
        id=row["id"],
        ts=clean.ts,
        side=clean.side,
        symbol=clean.symbol,
        shares=clean.shares,
        price=clean.price,
        amount=clean.amount,
        realized_pl=realized_pl,
        note=clean.note,
    )


def delete_transaction(txn_id: int) -> None:
    """Delete the ledger row only; holdings and cash effects are not reversed."""
    with db.connect() as c:
        c.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))


def list_transactions(limit: int = 200, symbol: str | None = None) -> list[Transaction]:
    """List transactions newest first by transaction date, then row id."""
    init_transactions_db()
    clean_limit = max(1, min(limit, 10_000))
    params: list[object] = []
    where = ""
    if symbol:
        where = "WHERE symbol = ?"
        params.append(symbol.strip().upper())
    params.append(clean_limit)
    with db.connect() as c:
        rows = c.execute(
            f"""
            SELECT id, ts, side, symbol, shares, price, amount, realized_pl, note
            FROM transactions
            {where}
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [
        Transaction(
            id=row["id"],
            ts=row["ts"],
            side=row["side"],
            symbol=row["symbol"],
            shares=row["shares"],
            price=row["price"],
            amount=row["amount"],
            realized_pl=row["realized_pl"],
            note=row["note"] or "",
        )
        for row in rows
    ]


def xirr(flows: list[tuple[str, float]]) -> float | None:
    """Annualized money-weighted return via bisection on NPV."""
    if len(flows) < 2:
        return None
    dated: list[tuple[date, float]] = []
    for ts, amount in flows:
        try:
            dated.append((date.fromisoformat(ts), float(amount)))
        except (TypeError, ValueError):
            return None
    if not any(amount < 0 for _, amount in dated) or not any(amount > 0 for _, amount in dated):
        return None

    first = min(day for day, _ in dated)
    last = max(day for day, _ in dated)
    if (last - first).days < 14:
        return None

    def npv(rate: float) -> float:
        return sum(
            amount / ((1 + rate) ** ((day - first).days / 365.25))
            for day, amount in dated
        )

    low = -0.9999
    high = 10.0
    npv_low = npv(low)
    npv_high = npv(high)
    if npv_low == 0:
        return low
    if npv_high == 0:
        return high
    if npv_low * npv_high > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2
        npv_mid = npv(mid)
        if abs(npv_mid) < 1e-7 or (high - low) / 2 < 1e-7:
            return mid
        if npv_low * npv_mid > 0:
            low = mid
            npv_low = npv_mid
        else:
            high = mid
    return (low + high) / 2


def compute_returns(valuation: PortfolioValuation) -> ReturnsSummary:
    """Compute realized P/L rollups and MWR from external cash flows plus terminal NAV."""
    txns = list_transactions(limit=10_000)
    external_flows: list[tuple[str, float]] = []
    total_deposited = 0.0
    total_withdrawn = 0.0
    realized_pl_by_year: dict[str, float] = {}
    realized_pl_total = 0.0

    for txn in reversed(txns):
        if txn.side == "deposit":
            total_deposited += txn.amount
            external_flows.append((txn.ts, -txn.amount))
        elif txn.side == "withdraw":
            total_withdrawn += txn.amount
            external_flows.append((txn.ts, txn.amount))
        elif txn.side == "sell" and txn.realized_pl is not None:
            year = txn.ts[:4]
            realized_pl_by_year[year] = _round_cents(
                realized_pl_by_year.get(year, 0.0) + txn.realized_pl
            )
            realized_pl_total += txn.realized_pl

    flow_count = len(external_flows)
    first_flow_date = external_flows[0][0] if external_flows else None
    mwr: float | None = None
    mwr_note = ""
    flows = list(external_flows)

    if valuation.unpriced_symbols:
        mwr_note = "portfolio not fully priced"
    elif not external_flows:
        mwr_note = "record deposits/withdrawals to compute money-weighted return"
    else:
        flows.append((datetime.now(UTC).date().isoformat(), valuation.total_with_cash))
        flow_count = len(flows)
        mwr = xirr(flows)
        if mwr is None:
            mwr_note = "not enough cash-flow history to annualize money-weighted return"

    return ReturnsSummary(
        mwr_annualized=mwr,
        mwr_note=mwr_note,
        total_deposited=_round_cents(total_deposited),
        total_withdrawn=_round_cents(total_withdrawn),
        realized_pl_total=_round_cents(realized_pl_total),
        realized_pl_by_year=dict(sorted(realized_pl_by_year.items())),
        first_flow_date=first_flow_date,
        flow_count=flow_count,
    )
