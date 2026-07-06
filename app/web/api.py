"""JSON API routes for the SPA."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from math import isfinite
from typing import Literal

import anyio
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..broker.sync import LAST_BROKER_SYNC_KEY, SyncResult, run_sync
from ..llm.budget import BudgetExceededError
from ..llm.discovery import discover_ideas
from ..llm.pipeline import InsufficientDataError, research_ticker_cached
from ..llm.usage import format_event, with_run
from ..portfolio.decision_support import (
    DISCLAIMER as DS_DISCLAIMER,
)
from ..portfolio.decision_support import (
    CorrelationInsight,
    DriftAnalysis,
    PortfolioHealth,
    PositionSizeGuidance,
    RegimeSignal,
    analyze_drift,
    assess_portfolio_health,
    compute_correlation_insight,
    compute_regime_signal,
    suggest_position_size,
)
from ..portfolio.holdings import PortfolioValuation, remove_holding, upsert_holding, value_holdings
from ..portfolio.optimize import Holding, NoDataError, OptimizeRequest, OptimizeResult, optimize
from ..portfolio.performance import BACKTEST_CAVEAT, PerformanceMetrics, compute_performance, tearsheet_html
from ..portfolio.plan import (
    ContributionPlan,
    RebalancePlan,
    Target,
    list_targets,
    plan_contribution,
    plan_rebalance,
    set_targets,
)
from ..portfolio.snapshots import NavSeries, build_nav_series, list_snapshots, record_snapshot
from ..portfolio.tax import TaxSignals, compute_tax_signals
from ..portfolio.transactions import (
    ReturnsSummary,
    Transaction,
    apply_transaction,
    compute_returns,
    delete_transaction,
    init_transactions_db,
    list_transactions,
)
from ..portfolio.twr import TWRSummary, compute_twr_summary
from ..profiles import PENNY_PRICE_MAX, PROFILES
from ..schemas import Confidence, DiscoveryResult, ResearchResult
from .imports import CsvImportError, ImportSummary, import_holdings_csv, import_transactions_csv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

EXAMPLES = [
    "AI infrastructure under $100B market cap",
    "Undervalued large caps",
    "Growth technology stocks",
    "Most active stocks today",
]

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
LLM_CONFIGURED = bool(config.OPENROUTER_API_KEY)
OPTIMIZER_EXCLUSION_WARNING = (
    "excluded from mean-variance optimization: sample statistics on illiquid micro-caps "
    "are unreliable"
)


def _effective_confidence(report: Confidence, critic: Confidence) -> Confidence:
    return min(report, critic, key=_CONF_ORDER.__getitem__)


def _scorecard_indicator_value(result: ResearchResult, key: str) -> float | None:
    if result.scorecard is None:
        return None
    return next((i.value for i in result.scorecard.indicators if i.key == key), None)


def _optimizer_exclusion_warnings(symbols: list[str]) -> list[str]:
    return [f"{symbol}: {OPTIMIZER_EXCLUSION_WARNING}" for symbol in symbols]


class MetaResponse(BaseModel):
    llm_configured: bool
    examples: list[str]
    broker_sync_configured: bool
    last_broker_sync: str | None


class DiscoverRequest(BaseModel):
    goal: str


class DiscoverResponse(BaseModel):
    result: DiscoveryResult
    watched_symbols: list[str]


class ResearchResponse(BaseModel):
    result: ResearchResult
    sizing: PositionSizeGuidance
    watched: bool
    profile_label: str
    effective_confidence: Confidence


class WatchlistResponse(BaseModel):
    items: list[db.WatchItem]


class WatchStatus(BaseModel):
    symbol: str
    watched: bool


class AllocationSlice(BaseModel):
    label: str
    value: float


class OptimizerRow(BaseModel):
    symbol: str
    value: float | None
    price: float | None


class PortfolioSummary(BaseModel):
    valuation: PortfolioValuation
    health: PortfolioHealth | None
    allocation: list[AllocationSlice]
    optimizer_seed: list[OptimizerRow]
    broker_sync_configured: bool
    last_broker_sync: str | None
    disclaimer: str


class HoldingsResponse(BaseModel):
    valuation: PortfolioValuation
    import_summary: ImportSummary | None = None


class HoldingUpdate(BaseModel):
    symbol: str
    shares: float
    avg_cost: float | None = None


class CashUpdate(BaseModel):
    cash: float


class TransactionsResponse(BaseModel):
    valuation: PortfolioValuation
    returns: ReturnsSummary
    transactions: list[Transaction]
    import_summary: ImportSummary | None = None


class TransactionCreate(BaseModel):
    ts: str
    side: str
    symbol: str | None = None
    shares: float | None = None
    price: float | None = None
    amount: float | None = None
    note: str = ""


class TargetRow(BaseModel):
    symbol: str
    weight_pct: float | None


class TargetsResponse(BaseModel):
    targets: list[Target]
    rows: list[TargetRow]
    implicit_cash_weight: float


class TargetInput(BaseModel):
    symbol: str
    weight_pct: float


class TargetsUpdate(BaseModel):
    targets: list[TargetInput]


class RebalanceResponse(BaseModel):
    plan: RebalancePlan | None
    has_targets: bool
    disclaimer: str


class WhatIfRequest(BaseModel):
    amount: float


class WhatIfResponse(BaseModel):
    plan: ContributionPlan | None
    has_targets: bool
    disclaimer: str


class NavResponse(BaseModel):
    series: NavSeries
    returns: ReturnsSummary


class CorrelationResponse(BaseModel):
    insight: CorrelationInsight | None
    symbols: list[str]
    too_few: bool


class RegimeResponse(BaseModel):
    signal: RegimeSignal | None
    has_holdings: bool


class PerformanceResponse(BaseModel):
    has_holdings: bool
    metrics: PerformanceMetrics | None
    backtest_caveat: str


class OptimizeHoldingInput(BaseModel):
    symbol: str
    value: float | None = None
    price: float | None = None


class OptimizeApiRequest(BaseModel):
    holdings: list[OptimizeHoldingInput] = Field(default_factory=list)
    objective: str = "max_sharpe"


class OptimizeResponse(BaseModel):
    available: bool
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    result: OptimizeResult | None = None
    drift: DriftAnalysis | None = None


class BrokerSyncResponse(BaseModel):
    configured: bool
    last_sync: str | None
    result: SyncResult


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


async def api_exception_handler(request: Request, exc: Exception) -> JSONResponse | PlainTextResponse:
    if request.url.path.startswith("/api"):
        logger.exception(
            "unhandled api error",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "internal",
                    "message": "Internal error — see server logs.",
                }
            },
        )
    return PlainTextResponse("Internal Server Error", status_code=500)


def _budget_message(e: BudgetExceededError) -> str:
    return (
        f"Daily LLM budget reached (${e.spent:.2f} of ${e.limit:.2f}). "
        "Resets at midnight UTC; cached reports still load."
    )


def _invalid(message: str) -> HTTPException:
    return _api_error(400, "invalid_input", message)


async def _holdings_response(import_summary: ImportSummary | None = None) -> HoldingsResponse:
    return HoldingsResponse(valuation=await value_holdings(), import_summary=import_summary)


async def _transactions_response(
    import_summary: ImportSummary | None = None,
) -> TransactionsResponse:
    init_transactions_db()
    valuation = await value_holdings()
    return TransactionsResponse(
        valuation=valuation,
        returns=compute_returns(valuation),
        transactions=list_transactions(limit=20),
        import_summary=import_summary,
    )


async def _targets_response() -> TargetsResponse:
    targets = list_targets()
    valuation = await value_holdings()
    target_map = {target.symbol: target.target_weight for target in targets}
    rows = [
        TargetRow(symbol=target.symbol, weight_pct=target.target_weight * 100)
        for target in targets
    ]
    rows.extend(
        TargetRow(symbol=holding.symbol, weight_pct=None)
        for holding in valuation.holdings
        if holding.symbol not in target_map
    )
    if not rows:
        rows.append(TargetRow(symbol="", weight_pct=None))
    total_target = sum(target_map.values())
    return TargetsResponse(
        targets=targets,
        rows=rows,
        implicit_cash_weight=max(0.0, 1.0 - total_target),
    )


def _allocation(valuation: PortfolioValuation) -> list[AllocationSlice]:
    slices = [
        AllocationSlice(label=h.symbol, value=h.market_value)
        for h in valuation.holdings
        if h.market_value is not None and h.market_value > 0
    ]
    if valuation.cash > 0:
        slices.append(AllocationSlice(label="Cash", value=valuation.cash))
    return slices


@router.get("/meta", response_model=MetaResponse)
def meta() -> MetaResponse:
    return MetaResponse(
        llm_configured=LLM_CONFIGURED,
        examples=EXAMPLES,
        broker_sync_configured=config.SNAPTRADE_CONFIGURED,
        last_broker_sync=db.get_setting("last_broker_sync"),
    )


@router.post("/discover", response_model=DiscoverResponse)
async def discover(request: DiscoverRequest) -> DiscoverResponse:
    goal = request.goal.strip()
    if not goal:
        raise _invalid("Goal is required.")
    try:
        async with with_run("discover", goal) as ctx:
            result = await discover_ideas(goal)
        print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
        watched = sorted(i.symbol for i in db.list_items())
        return DiscoverResponse(result=result, watched_symbols=watched)
    except BudgetExceededError as e:
        raise _api_error(429, "budget_exceeded", _budget_message(e)) from e
    except Exception as e:
        logger.exception("discover failed")
        raise _api_error(500, "internal", "Discovery failed — see server logs.") from e


@router.get("/research/{symbol}", response_model=ResearchResponse)
async def research(
    symbol: str,
    mode: str = "thorough",
    fresh: int = 0,
    profile: Literal["penny", "largecap"] | None = Query(default=None),
) -> ResearchResponse:
    sym = symbol.strip().upper()
    mode = "cheap" if mode == "cheap" else "thorough"
    try:
        async with with_run("research", sym, mode) as ctx:
            result = await research_ticker_cached(
                sym,
                mode,
                profile_override=profile,
                fresh=bool(fresh),
            )
        print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
        valuation = await value_holdings()
        effective = _effective_confidence(
            result.report.confidence, result.critique.suggested_confidence
        )
        current_weight = next((h.weight for h in valuation.holdings if h.symbol == sym), None)
        result_profile = PROFILES[result.profile]
        adv_dollars = _scorecard_indicator_value(result, "avg_dollar_volume_20d")
        sizing = suggest_position_size(
            valuation.total_with_cash,
            effective,
            symbol=sym,
            current_weight=current_weight,
            profile=result_profile,
            adv_dollars=adv_dollars,
        )
        return ResearchResponse(
            result=result,
            sizing=sizing,
            watched=db.has(sym),
            profile_label=result_profile.label,
            effective_confidence=effective,
        )
    except BudgetExceededError as e:
        raise _api_error(429, "budget_exceeded", _budget_message(e)) from e
    except InsufficientDataError as e:
        logger.exception("research failed: insufficient data for %s", sym)
        raise _api_error(
            404,
            "insufficient_data",
            f"No market data found for {sym} — check the ticker symbol.",
        ) from e
    except Exception as e:
        logger.exception("research failed for %s", sym)
        raise _api_error(500, "internal", "Research failed — see server logs.") from e


@router.get("/watchlist", response_model=WatchlistResponse)
def watchlist() -> WatchlistResponse:
    return WatchlistResponse(items=db.list_items())


@router.put("/watchlist/{symbol}", response_model=WatchStatus)
def watchlist_add(symbol: str) -> WatchStatus:
    sym = symbol.strip().upper()
    if not sym:
        raise _invalid("symbol must be non-empty")
    if not db.has(sym):
        db.add(sym)
    return WatchStatus(symbol=sym, watched=True)


@router.delete("/watchlist/{symbol}", response_model=WatchStatus)
def watchlist_delete(symbol: str) -> WatchStatus:
    sym = symbol.strip().upper()
    db.remove(sym)
    return WatchStatus(symbol=sym, watched=False)


@router.get("/portfolio", response_model=PortfolioSummary)
async def portfolio() -> PortfolioSummary:
    valuation = await value_holdings()
    try:
        record_snapshot(valuation)
    except Exception:
        logger.warning("nav snapshot write failed", exc_info=True)
    optimizer_rows = [
        OptimizerRow(
            symbol=v.symbol,
            value=round(v.market_value, 2) if v.market_value is not None else None,
            price=v.price,
        )
        for v in valuation.holdings
    ]
    return PortfolioSummary(
        valuation=valuation,
        optimizer_seed=optimizer_rows,
        health=assess_portfolio_health(valuation),
        allocation=_allocation(valuation),
        disclaimer=DS_DISCLAIMER,
        broker_sync_configured=config.SNAPTRADE_CONFIGURED,
        last_broker_sync=db.get_setting("last_broker_sync"),
    )


@router.get("/portfolio/holdings", response_model=HoldingsResponse)
async def holdings() -> HoldingsResponse:
    return await _holdings_response()


@router.put("/portfolio/holdings", response_model=HoldingsResponse)
async def holdings_put(request: HoldingUpdate) -> HoldingsResponse:
    sym = request.symbol.strip().upper()
    if not sym:
        raise _invalid("symbol must be non-empty")
    if not isfinite(request.shares):
        raise _invalid("shares must be a finite number")
    if request.avg_cost is not None and not isfinite(request.avg_cost):
        raise _invalid("avg_cost must be a finite number")
    upsert_holding(sym, request.shares, request.avg_cost)
    return await _holdings_response()


@router.delete("/portfolio/holdings/{symbol}", response_model=HoldingsResponse)
async def holdings_delete(symbol: str) -> HoldingsResponse:
    remove_holding(symbol.strip().upper())
    return await _holdings_response()


@router.put("/portfolio/cash", response_model=HoldingsResponse)
async def cash_put(request: CashUpdate) -> HoldingsResponse:
    if not isfinite(request.cash):
        raise _invalid("cash must be a finite number")
    try:
        db.set_cash(request.cash)
    except ValueError as e:
        raise _invalid(str(e)) from e
    return await _holdings_response()


@router.post("/portfolio/holdings/import", response_model=HoldingsResponse)
async def holdings_import(file: UploadFile = File(...)) -> HoldingsResponse:
    try:
        summary = import_holdings_csv(await file.read())
        return await _holdings_response(summary)
    except CsvImportError as e:
        raise _invalid(str(e)) from e


@router.get("/portfolio/transactions", response_model=TransactionsResponse)
async def transactions() -> TransactionsResponse:
    try:
        return await _transactions_response()
    except Exception as e:
        logger.exception("portfolio transactions failed")
        raise _api_error(500, "internal", "Transactions failed — see server logs.") from e


@router.post("/portfolio/transactions", response_model=TransactionsResponse)
async def transaction_add(request: TransactionCreate) -> TransactionsResponse:
    try:
        side_clean = request.side.strip().lower()
        parsed_amount = (
            float(request.shares or 0) * float(request.price or 0)
            if side_clean in {"buy", "sell"}
            else float(request.amount)
        )
        apply_transaction(
            Transaction(
                ts=request.ts.strip(),
                side=side_clean,
                symbol=request.symbol.strip().upper() if request.symbol else None,
                shares=request.shares,
                price=request.price,
                amount=parsed_amount,
                realized_pl=None,
                note=request.note.strip(),
            )
        )
        return await _transactions_response()
    except (TypeError, ValueError) as e:
        raise _invalid(str(e)) from e
    except Exception as e:
        logger.exception("portfolio transaction add failed")
        raise _api_error(500, "internal", "Transaction failed — see server logs.") from e


@router.delete("/portfolio/transactions/{txn_id}", response_model=TransactionsResponse)
async def transaction_delete(txn_id: int) -> TransactionsResponse:
    try:
        delete_transaction(txn_id)
        return await _transactions_response()
    except Exception as e:
        logger.exception("portfolio transaction delete failed")
        raise _api_error(500, "internal", "Transaction delete failed — see server logs.") from e


@router.post("/portfolio/transactions/import", response_model=TransactionsResponse)
async def transactions_import(file: UploadFile = File(...)) -> TransactionsResponse:
    try:
        summary = import_transactions_csv(await file.read())
        return await _transactions_response(summary)
    except CsvImportError as e:
        raise _invalid(str(e)) from e
    except Exception as e:
        logger.exception("portfolio transaction import failed")
        raise _api_error(500, "internal", "Transaction import failed — see server logs.") from e


@router.get("/portfolio/targets", response_model=TargetsResponse)
async def targets_get() -> TargetsResponse:
    try:
        return await _targets_response()
    except Exception as e:
        logger.exception("portfolio targets failed")
        raise _api_error(500, "internal", "Target allocations failed — see server logs.") from e


@router.put("/portfolio/targets", response_model=TargetsResponse)
async def targets_put(request: TargetsUpdate) -> TargetsResponse:
    try:
        targets = [
            Target(symbol=t.symbol, target_weight=t.weight_pct / 100)
            for t in request.targets
            if t.symbol.strip()
        ]
        set_targets(targets)
        return await _targets_response()
    except ValueError as e:
        raise _invalid(str(e)) from e
    except Exception as e:
        logger.exception("portfolio targets update failed")
        raise _api_error(500, "internal", "Target update failed — see server logs.") from e


@router.get("/portfolio/rebalance", response_model=RebalanceResponse)
async def rebalance() -> RebalanceResponse:
    try:
        targets = list_targets()
        valuation = await value_holdings()
        return RebalanceResponse(
            plan=plan_rebalance(valuation, targets),
            has_targets=bool(targets),
            disclaimer=DS_DISCLAIMER,
        )
    except Exception as e:
        logger.exception("portfolio rebalance failed")
        raise _api_error(500, "internal", "Rebalance plan failed — see server logs.") from e


@router.post("/portfolio/whatif", response_model=WhatIfResponse)
async def whatif(request: WhatIfRequest) -> WhatIfResponse:
    if request.amount <= 0 or not isfinite(request.amount):
        raise _invalid("Contribution amount must be a positive number.")
    try:
        targets = list_targets()
        valuation = await value_holdings()
        return WhatIfResponse(
            plan=plan_contribution(valuation, targets, request.amount),
            has_targets=bool(targets),
            disclaimer=DS_DISCLAIMER,
        )
    except Exception as e:
        logger.exception("portfolio what-if failed")
        raise _api_error(500, "internal", "Contribution preview failed — see server logs.") from e


@router.get("/portfolio/nav", response_model=NavResponse)
async def nav() -> NavResponse:
    try:
        valuation = await value_holdings()
        return NavResponse(
            series=build_nav_series(list_snapshots()),
            returns=compute_returns(valuation),
        )
    except Exception as e:
        logger.exception("portfolio nav history failed")
        raise _api_error(500, "internal", "Portfolio NAV history failed — see server logs.") from e


@router.get("/portfolio/correlation", response_model=CorrelationResponse)
async def correlation() -> CorrelationResponse:
    try:
        valuation = await value_holdings()
        ordered_holdings = sorted(
            valuation.holdings,
            key=lambda h: h.weight if h.weight is not None else -1.0,
            reverse=True,
        )
        symbols = [h.symbol for h in ordered_holdings]
        insight = await compute_correlation_insight(symbols) if len(symbols) >= 2 else None
        return CorrelationResponse(insight=insight, symbols=symbols, too_few=len(symbols) < 2)
    except Exception as e:
        logger.exception("portfolio correlation failed")
        raise _api_error(500, "internal", "Portfolio correlation failed — see server logs.") from e


@router.get("/portfolio/regime", response_model=RegimeResponse)
async def regime() -> RegimeResponse:
    try:
        valuation = await value_holdings()
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        signal = await compute_regime_signal(weights) if weights else None
        return RegimeResponse(signal=signal, has_holdings=bool(valuation.holdings))
    except Exception as e:
        logger.exception("portfolio volatility regime failed")
        raise _api_error(
            500,
            "internal",
            "Portfolio volatility regime failed — see server logs.",
        ) from e


@router.get("/portfolio/tax", response_model=TaxSignals)
async def tax() -> TaxSignals:
    try:
        valuation = await value_holdings()
        return compute_tax_signals(
            valuation,
            list_transactions(limit=10_000),
            datetime.now(UTC).date(),
        )
    except Exception as e:
        logger.exception("portfolio tax signals failed")
        raise _api_error(500, "internal", "Tax flags failed — see server logs.") from e


@router.get("/portfolio/performance", response_model=PerformanceResponse)
async def performance() -> PerformanceResponse:
    try:
        valuation = await value_holdings()
        has_holdings = bool(valuation.holdings)
        if not has_holdings:
            return PerformanceResponse(
                has_holdings=False,
                metrics=None,
                backtest_caveat=BACKTEST_CAVEAT,
            )
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        metrics = await compute_performance(weights) if weights else None
        return PerformanceResponse(
            has_holdings=True,
            metrics=metrics,
            backtest_caveat=BACKTEST_CAVEAT,
        )
    except Exception as e:
        logger.exception("portfolio performance failed")
        raise _api_error(500, "internal", "Portfolio performance failed — see server logs.") from e


@router.get("/portfolio/twr", response_model=TWRSummary | None)
async def twr() -> TWRSummary | None:
    try:
        return await compute_twr_summary()
    except Exception as e:
        logger.exception("portfolio twr failed")
        raise _api_error(500, "internal", "Portfolio TWR failed — see server logs.") from e


@router.get("/portfolio/tearsheet", response_class=HTMLResponse, include_in_schema=False)
async def tearsheet() -> HTMLResponse:
    try:
        valuation = await value_holdings()
        if not valuation.holdings:
            return HTMLResponse("<p>No holdings — add positions first.</p>")
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        if not weights:
            return HTMLResponse("<p>Unable to compute weights — prices may be unavailable.</p>")
        html = await anyio.to_thread.run_sync(lambda: tearsheet_html(weights))
        if not html:
            return HTMLResponse("<p>Could not generate tearsheet — insufficient price history.</p>")
        return HTMLResponse(content=html)
    except Exception as e:
        logger.exception("portfolio tearsheet failed")
        raise _api_error(500, "internal", "Portfolio tearsheet failed — see server logs.") from e


@router.post("/portfolio/optimize", response_model=OptimizeResponse)
async def optimize_portfolio(request: OptimizeApiRequest) -> OptimizeResponse:
    excluded_symbols: list[str] = []

    try:
        if not any((h.symbol or "").strip() for h in request.holdings):
            valuation = await value_holdings()
            holdings: list[Holding] = []
            for v in valuation.holdings:
                if v.price is not None and v.price < PENNY_PRICE_MAX:
                    excluded_symbols.append(v.symbol)
                    continue
                if v.market_value:
                    holdings.append(Holding(symbol=v.symbol, value=v.market_value))
        else:
            holdings = []
            for input_holding in request.holdings:
                sym = (input_holding.symbol or "").strip().upper()
                if not sym:
                    continue
                if input_holding.price is not None and input_holding.price < PENNY_PRICE_MAX:
                    excluded_symbols.append(sym)
                    continue
                holdings.append(Holding(symbol=sym, value=input_holding.value))
    except ValueError as e:
        raise _invalid(str(e)) from e
    except Exception as e:
        logger.exception("portfolio optimizer failed")
        raise _api_error(500, "internal", "Optimization failed — see server logs.") from e

    exclusion_warnings = _optimizer_exclusion_warnings(excluded_symbols)

    if not holdings:
        reason = OPTIMIZER_EXCLUSION_WARNING if excluded_symbols else "No symbols provided."
        return OptimizeResponse(available=False, reason=reason, warnings=exclusion_warnings)

    if excluded_symbols and len({h.symbol for h in holdings}) < 2:
        return OptimizeResponse(
            available=False,
            reason=OPTIMIZER_EXCLUSION_WARNING,
            warnings=exclusion_warnings,
        )

    try:
        req = OptimizeRequest(holdings=holdings, objective=request.objective)
    except ValueError as e:
        raise _invalid(str(e)) from e
    try:
        result = await anyio.to_thread.run_sync(optimize, req)
    except NoDataError as e:
        return OptimizeResponse(available=False, reason=str(e), warnings=exclusion_warnings)
    except Exception as e:
        return OptimizeResponse(
            available=False,
            reason=f"Optimization failed: {e}",
            warnings=exclusion_warnings,
        )
    drift = analyze_drift(result)
    result.warnings = exclusion_warnings + result.warnings
    return OptimizeResponse(available=True, warnings=result.warnings, result=result, drift=drift)


@router.post("/portfolio/broker/sync", response_model=BrokerSyncResponse)
async def broker_sync() -> BrokerSyncResponse:
    try:
        result = await run_sync(dry_run=False)
        return BrokerSyncResponse(
            configured=config.SNAPTRADE_CONFIGURED,
            last_sync=db.get_setting(LAST_BROKER_SYNC_KEY),
            result=result,
        )
    except Exception as e:
        logger.exception("broker sync failed")
        raise _api_error(500, "internal", "Broker sync failed — see server logs.") from e
