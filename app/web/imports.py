"""Shared CSV import helpers for web and JSON routes."""

from __future__ import annotations

from csv import Error as CsvError
from csv import reader as csv_reader
from io import StringIO
from math import isfinite

from pydantic import BaseModel

from ..portfolio.holdings import upsert_holding
from ..portfolio.transactions import Transaction, apply_transaction

MAX_IMPORT_BYTES = 100 * 1024
MAX_IMPORT_ROWS = 500


class CsvImportError(ValueError):
    """Fatal CSV import problem with an already user-facing message."""


class ImportSummary(BaseModel):
    imported: int
    skipped: list[str]


def _rows_from_csv(raw: bytes) -> list[list[str]]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise CsvImportError("CSV import failed: file must be 100 KB or smaller.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvImportError("CSV import failed: file must be UTF-8 encoded.") from exc

    try:
        rows = list(csv_reader(StringIO(text)))
    except CsvError as exc:
        raise CsvImportError("CSV import failed: unable to parse CSV.") from exc

    if not rows:
        raise CsvImportError("CSV import failed: header row is required.")

    data_rows = rows[1:]
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise CsvImportError("CSV import failed: maximum 500 data rows allowed.")

    return rows


def _column_map(rows: list[list[str]], required: tuple[str, ...], message: str) -> dict[str, int]:
    header = [col.strip().lower() for col in rows[0]]
    column_map = {name: index for index, name in enumerate(header) if name}
    if any(name not in column_map for name in required):
        raise CsvImportError(message)
    return column_map


def import_holdings_csv(raw: bytes) -> ImportSummary:
    rows = _rows_from_csv(raw)
    column_map = _column_map(
        rows,
        ("symbol", "shares"),
        "CSV import failed: header must include symbol and shares columns.",
    )

    imported = 0
    skipped: list[str] = []
    symbol_index = column_map["symbol"]
    shares_index = column_map["shares"]
    avg_cost_index = column_map.get("avg_cost")

    for line_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue

        symbol = row[symbol_index].strip().upper() if symbol_index < len(row) else ""
        if not symbol:
            skipped.append(f"line {line_number}: missing symbol")
            continue

        raw_shares = row[shares_index].strip() if shares_index < len(row) else ""
        try:
            shares = float(raw_shares)
        except ValueError:
            skipped.append(f"line {line_number}: bad shares '{raw_shares}'")
            continue
        if shares <= 0 or not isfinite(shares):
            skipped.append(f"line {line_number}: shares must be > 0")
            continue

        avg_cost: float | None = None
        if avg_cost_index is not None and avg_cost_index < len(row):
            raw_avg_cost = row[avg_cost_index].strip()
            if raw_avg_cost:
                try:
                    avg_cost = float(raw_avg_cost)
                except ValueError:
                    avg_cost = None
                if avg_cost is not None and not isfinite(avg_cost):
                    avg_cost = None

        upsert_holding(symbol, shares, avg_cost)
        imported += 1

    return ImportSummary(imported=imported, skipped=skipped)


def import_transactions_csv(raw: bytes) -> ImportSummary:
    rows = _rows_from_csv(raw)
    column_map = _column_map(
        rows,
        ("date", "side"),
        "CSV import failed: header must include date and side.",
    )

    def cell(row: list[str], name: str) -> str:
        index = column_map.get(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    imported = 0
    skipped: list[str] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if not any(col.strip() for col in row):
            continue
        try:
            side_clean = cell(row, "side").lower()
            raw_shares = cell(row, "shares")
            raw_price = cell(row, "price")
            shares_value = float(raw_shares) if raw_shares else None
            price_value = float(raw_price) if raw_price else None
            raw_amount = cell(row, "amount")
            amount_value = (
                float(shares_value or 0) * float(price_value or 0)
                if side_clean in {"buy", "sell"}
                else float(raw_amount)
            )
            apply_transaction(
                Transaction(
                    ts=cell(row, "date"),
                    side=side_clean,
                    symbol=cell(row, "symbol") or None,
                    shares=shares_value,
                    price=price_value,
                    amount=amount_value,
                    realized_pl=None,
                    note=cell(row, "note"),
                )
            )
            imported += 1
        except (TypeError, ValueError) as e:
            skipped.append(f"line {line_number}: {e}")

    return ImportSummary(imported=imported, skipped=skipped)
