"""SEC XBRL financials via edgartools (edgar library)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import pandas as pd
from pydantic import BaseModel

from ..cache import with_cache

logger = logging.getLogger(__name__)

_IDENTITY_SET = False
_SEC_TTL_MS = 24 * 60 * 60 * 1000  # 24 h
_PERIOD_COL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


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
    rows = df[(df["standard_concept"] == concept) & _dimensionless_mask(df)]
    if rows.empty:
        return None
    return _first_newest_value(df, rows)


def _period_columns(df: pd.DataFrame) -> list[str]:
    return sorted(
        [c for c in df.columns if _PERIOD_COL_RE.match(str(c))],
        key=lambda c: str(c)[:10],
        reverse=True,
    )


def _dimensionless_mask(df: pd.DataFrame) -> pd.Series:
    if "dimension" not in df.columns:
        return pd.Series(True, index=df.index)
    return ~df["dimension"].fillna("").astype(bool)


def _first_newest_value(df: pd.DataFrame, rows: pd.DataFrame) -> float | None:
    period_cols = _period_columns(df)
    if not period_cols:
        return None
    for v in rows[period_cols[0]]:
        if not pd.isna(v):
            return float(v)
    return None


def _get_by_tags(df: pd.DataFrame, tags: list[str]) -> float | None:
    for tag in tags:
        rows = df[(df["concept"] == tag) & _dimensionless_mask(df)]
        if rows.empty:
            continue
        value = _first_newest_value(df, rows)
        if value is not None:
            return value
    return None


def _get_tag_or_concept(df: pd.DataFrame, tags: list[str], concept: str) -> float | None:
    value = _get_by_tags(df, tags)
    return value if value is not None else _get_concept(df, concept)


def _sanity_check(result: dict[str, Any]) -> dict[str, Any]:
    clean = dict(result)
    total_assets = clean.get("total_assets")
    total_liabilities = clean.get("total_liabilities")
    if (
        total_assets is not None
        and total_liabilities is not None
        and total_assets < total_liabilities
    ):
        logger.warning(
            "SEC sanity check dropped assets/liabilities: assets below liabilities"
        )
        clean["total_assets"] = None
        clean["total_liabilities"] = None

    revenue = clean.get("revenue")
    net_income = clean.get("net_income")
    if (
        revenue is not None
        and net_income is not None
        and revenue > 0
        and net_income > 0
        and net_income > revenue
    ):
        logger.warning("SEC sanity check dropped revenue/net income: net income above revenue")
        clean["revenue"] = None
        clean["net_income"] = None
    return clean


def _fetch_blocking(symbol: str) -> dict[str, Any]:
    _ensure_identity()
    import edgar

    company = edgar.Company(symbol)
    if company.not_found:
        return {}

    fin = company.get_financials()
    if fin is None:
        return {}

    result: dict[str, Any] = {}

    try:
        df_inc = fin.income_statement().to_dataframe()
        result["revenue"] = _get_tag_or_concept(
            df_inc,
            [
                "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap_Revenues",
                "us-gaap_RevenuesNetOfInterestExpense",
                "us-gaap_SalesRevenueNet",
            ],
            "Revenue",
        )
        result["gross_profit"] = _get_tag_or_concept(
            df_inc, ["us-gaap_GrossProfit"], "GrossProfit"
        )
        result["operating_income"] = _get_tag_or_concept(
            df_inc, ["us-gaap_OperatingIncomeLoss"], "OperatingIncomeLoss"
        )
        result["net_income"] = _get_tag_or_concept(
            df_inc, ["us-gaap_NetIncomeLoss"], "NetIncome"
        )
    except Exception:
        logger.warning("SEC income statement fetch failed for %s", symbol, exc_info=True)

    try:
        df_bs = fin.balance_sheet().to_dataframe()
        result["total_assets"] = _get_tag_or_concept(
            df_bs, ["us-gaap_Assets"], "Assets"
        )
        result["total_liabilities"] = _get_tag_or_concept(
            df_bs, ["us-gaap_Liabilities"], "Liabilities"
        )
        result["cash_and_equivalents"] = _get_tag_or_concept(
            df_bs,
            [
                "us-gaap_CashAndCashEquivalentsAtCarryingValue",
                "us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            ],
            "CashAndMarketableSecurities",
        )
        result["shares_outstanding"] = _get_concept(df_bs, "SharesYearEnd")
        # total_debt = long-term + short-term
        ltd = _get_concept(df_bs, "LongTermDebt")
        std = _get_concept(df_bs, "ShortTermDebt")
        if ltd is not None or std is not None:
            result["total_debt"] = (ltd or 0.0) + (std or 0.0)
    except Exception:
        logger.warning("SEC balance sheet fetch failed for %s", symbol, exc_info=True)

    try:
        df_cf = fin.cashflow_statement().to_dataframe()
        ocf = _get_tag_or_concept(
            df_cf,
            [
                "us-gaap_NetCashProvidedByUsedInOperatingActivities",
                "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ],
            "NetCashFromOperatingActivities",
        )
        result["operating_cash_flow"] = ocf
        capex = _get_tag_or_concept(
            df_cf, ["us-gaap_PaymentsToAcquirePropertyPlantAndEquipment"], "CapitalExpenses"
        )
        if ocf is not None and capex is not None:
            # capex is stored as negative in XBRL; FCF = OCF - |capex|
            result["free_cash_flow"] = ocf - abs(capex)
    except Exception:
        logger.warning("SEC cashflow statement fetch failed for %s", symbol, exc_info=True)

    try:
        ei = fin.xb.entity_info
        result["fiscal_period"] = ei.get("fiscal_period")
        result["form"] = fin.xb.document_type
        end_date = ei.get("document_period_end_date")
        result["filed"] = str(end_date) if end_date else None
    except Exception:
        logger.warning("SEC entity info fetch failed for %s", symbol, exc_info=True)

    return _sanity_check(result)


async def fetch_financials(symbol: str, *, fresh: bool = False) -> SecFinancials | None:
    """Latest SEC XBRL financials for ``symbol``, cached 24h. ``None`` if the
    company isn't found in EDGAR or every statement fetch failed."""
    key = symbol.upper()

    async def produce() -> dict:
        return await asyncio.to_thread(_fetch_blocking, key)

    value, _ = await with_cache("sec", key, _SEC_TTL_MS, produce, fresh=fresh)

    if not value:
        return None

    try:
        return SecFinancials.model_validate(value)
    except Exception:
        return None
