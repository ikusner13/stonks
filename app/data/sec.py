"""SEC XBRL financials via edgartools (edgar library)."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import pandas as pd
from pydantic import BaseModel

from ..cache import with_cache

_IDENTITY_SET = False
_SEC_TTL_MS = 24 * 60 * 60 * 1000  # 24 h


def _ensure_identity() -> None:
    global _IDENTITY_SET
    if not _IDENTITY_SET:
        import edgar
        edgar.set_identity(os.getenv("SEC_IDENTITY", "ikusner13@gmail.com"))
        _IDENTITY_SET = True


class SecFinancials(BaseModel):
    revenue: float | None = None
    net_income: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    shares_outstanding: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    fiscal_period: str | None = None
    form: str | None = None
    filed: str | None = None

    def numeric_values(self) -> list[float]:
        _numeric_fields = {
            "revenue", "net_income", "gross_profit", "operating_income",
            "total_assets", "total_liabilities", "cash_and_equivalents",
            "total_debt", "shares_outstanding", "operating_cash_flow", "free_cash_flow",
        }
        return [
            v for k, v in self.model_dump().items()
            if k in _numeric_fields and v is not None
        ]


def _get_concept(df: pd.DataFrame, concept: str) -> float | None:
    rows = df[(df["standard_concept"] == concept) & (~df["dimension"].astype(bool))]
    if rows.empty:
        return None
    val_cols = [c for c in df.columns if re.match(r"\d{4}", c)]
    if not val_cols:
        return None
    v = rows[val_cols[0]].iloc[0]
    return None if pd.isna(v) else float(v)


def _fetch_blocking(symbol: str) -> dict[str, Any] | None:
    _ensure_identity()
    import edgar

    try:
        company = edgar.Company(symbol)
        if company.not_found:
            return None
    except Exception:
        return None

    try:
        fin = company.get_financials()
        if fin is None:
            return None
    except Exception:
        return None

    result: dict[str, Any] = {}

    try:
        df_inc = fin.income_statement().to_dataframe()
        result["revenue"] = _get_concept(df_inc, "Revenue")
        result["gross_profit"] = _get_concept(df_inc, "GrossProfit")
        result["operating_income"] = _get_concept(df_inc, "OperatingIncomeLoss")
        result["net_income"] = _get_concept(df_inc, "NetIncome")
    except Exception:
        pass

    try:
        df_bs = fin.balance_sheet().to_dataframe()
        result["total_assets"] = _get_concept(df_bs, "Assets")
        result["total_liabilities"] = _get_concept(df_bs, "Liabilities")
        result["cash_and_equivalents"] = _get_concept(df_bs, "CashAndMarketableSecurities")
        result["shares_outstanding"] = _get_concept(df_bs, "SharesYearEnd")
        # total_debt = long-term + short-term
        ltd = _get_concept(df_bs, "LongTermDebt")
        std = _get_concept(df_bs, "ShortTermDebt")
        if ltd is not None or std is not None:
            result["total_debt"] = (ltd or 0.0) + (std or 0.0)
    except Exception:
        pass

    try:
        df_cf = fin.cashflow_statement().to_dataframe()
        ocf = _get_concept(df_cf, "NetCashFromOperatingActivities")
        result["operating_cash_flow"] = ocf
        capex = _get_concept(df_cf, "CapitalExpenses")
        if ocf is not None and capex is not None:
            # capex is stored as negative in XBRL; FCF = OCF - |capex|
            result["free_cash_flow"] = ocf - abs(capex)
    except Exception:
        pass

    try:
        ei = fin.xb.entity_info
        result["fiscal_period"] = ei.get("fiscal_period")
        result["form"] = fin.xb.document_type
        end_date = ei.get("document_period_end_date")
        result["filed"] = str(end_date) if end_date else None
    except Exception:
        pass

    return result


async def fetch_financials(symbol: str, *, fresh: bool = False) -> SecFinancials | None:
    key = symbol.upper()

    async def produce() -> dict:
        data = await asyncio.to_thread(_fetch_blocking, key)
        if data is None:
            return {}
        return data

    value, _ = await with_cache("sec", key, _SEC_TTL_MS, produce, fresh=fresh)

    if not value:
        return None

    try:
        return SecFinancials.model_validate(value)
    except Exception:
        return None
