"""FastAPI app: server-rendered pages + HTMX partials. Folds in the portfolio
optimizer (formerly an HTTP sidecar) as a direct in-process call."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config, db
from ..alerts import init_alerts_db
from ..jobs import build_jobs, scheduler_loop
from ..llm.budget import BudgetExceededError
from ..llm.discovery import discover_ideas
from ..llm.pipeline import InsufficientDataError, research_ticker_cached
from ..llm.usage import format_event, with_run
from ..profiles import PENNY_PRICE_MAX, PROFILES
from ..portfolio.decision_support import (
    DISCLAIMER as DS_DISCLAIMER,
)
from ..portfolio.decision_support import (
    analyze_drift,
    assess_portfolio_health,
    compute_correlation_insight,
    compute_regime_signal,
    suggest_position_size,
)
from ..portfolio.holdings import (
    init_holdings_db,
    value_holdings,
)
from ..portfolio.optimize import Holding, NoDataError, OptimizeRequest, optimize
from ..portfolio.plan import (
    Target,
    init_targets_db,
    list_targets,
    plan_contribution,
    plan_rebalance,
    set_targets,
)
from ..portfolio.performance import BACKTEST_CAVEAT, compute_performance, tearsheet_html
from ..portfolio.snapshots import (
    build_nav_series,
    init_snapshots_db,
    list_snapshots,
    record_snapshot,
)
from ..portfolio.tax import compute_tax_signals
from ..portfolio.transactions import (
    compute_returns,
    init_transactions_db,
    list_transactions,
)
from ..portfolio.twr import compute_twr_summary
from ..schemas import Confidence
from .charts import corr_color, donut, frontier_chart, nav_area

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")

EXAMPLES = [
    "AI infrastructure under $100B market cap",
    "Undervalued large caps",
    "Growth technology stocks",
    "Most active stocks today",
]

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
LLM_CONFIGURED = bool(config.OPENROUTER_API_KEY)
if not LLM_CONFIGURED:
    logger.warning("OPENROUTER_API_KEY not set — research and discovery will fail")

OPTIMIZER_EXCLUSION_WARNING = (
    "excluded from mean-variance optimization: sample statistics on illiquid micro-caps "
    "are unreliable"
)

# --- Jinja filters ----------------------------------------------------------


def _fmt_num(n: float | None) -> str:
    if n is None:
        return "n/a"
    return f"{n:,.2f}".rstrip("0").rstrip(".") if n % 1 else f"{n:,.0f}"


def _fmt_cap(n: float | None) -> str:
    if n is None:
        return "n/a"
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"


def _pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def _fmt_indicator_value(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "pct":
        return _pct(value)
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "usd":
        sign = "-" if value < 0 else ""
        n = abs(value)
        for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if n >= div:
                scaled = f"{n / div:.1f}".rstrip("0").rstrip(".")
                return f"{sign}${scaled}{suffix}"
        return f"{sign}${n:,.0f}"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:,.0f}"


def _effective_confidence(report: Confidence, critic: Confidence) -> Confidence:
    return min(report, critic, key=_CONF_ORDER.__getitem__)


def _scorecard_indicator_value(result, key: str) -> float | None:
    if result.scorecard is None:
        return None
    return next((i.value for i in result.scorecard.indicators if i.key == key), None)


def _optimizer_exclusion_warnings(symbols: list[str]) -> list[str]:
    return [f"{symbol}: {OPTIMIZER_EXCLUSION_WARNING}" for symbol in symbols]


def _allocation_slices(valuation) -> list:
    slices = [
        (h.symbol, h.market_value)
        for h in valuation.holdings
        if h.market_value is not None and h.market_value > 0
    ]
    if valuation.cash > 0:
        slices.append(("Cash", valuation.cash))
    return donut(slices)


def _error_partial(
    request: Request,
    message: str,
    retry_url: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/error.html",
        {"message": message, "retry_url": retry_url},
    )


templates.env.filters["fmt_num"] = _fmt_num
templates.env.filters["fmt_cap"] = _fmt_cap
templates.env.filters["pct"] = _pct
templates.env.filters["indicator_value"] = _fmt_indicator_value


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task: asyncio.Task | None = None
    jobs_registry = build_jobs()
    if jobs_registry:
        task = asyncio.create_task(scheduler_loop(jobs_registry))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Stock Research", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

db.init_db()  # idempotent; ensures the watchlist table exists before first request
init_holdings_db()  # idempotent; ensures the holdings table exists before first request
init_snapshots_db()  # idempotent; ensures the NAV history table exists before first request
init_targets_db()  # idempotent; ensures target allocations exist before first request
init_transactions_db()  # idempotent; ensures the transaction ledger exists before first request
init_alerts_db()  # idempotent; ensures alert ranges and delivery ledger exist before first request


# --- Discover ---------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(HERE / "static" / "favicon.ico")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active": "discover", "examples": EXAMPLES, "llm_configured": LLM_CONFIGURED},
    )


@app.post("/discover", response_class=HTMLResponse)
async def discover(request: Request, goal: str = Form("")):
    goal = goal.strip()
    if not goal:
        return HTMLResponse("")
    try:
        async with with_run("discover", goal) as ctx:
            result = await discover_ideas(goal)
        print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
        watched = {i.symbol for i in db.list_items()}
        return templates.TemplateResponse(
            request, "partials/candidates.html", {"result": result, "watched_symbols": watched}
        )
    except BudgetExceededError as e:
        return _error_partial(
            request,
            f"Daily LLM budget reached (${e.spent:.2f} of ${e.limit:.2f}). "
            "Resets at midnight UTC; cached reports still load.",
        )
    except Exception:
        logger.exception("discover failed")
        return _error_partial(request, "Discovery failed — see server logs.")


# --- Research ---------------------------------------------------------------


@app.get("/research/{symbol}", response_class=HTMLResponse)
def research_page(request: Request, symbol: str, mode: str = "thorough"):
    return templates.TemplateResponse(
        request,
        "research.html",
        {
            "active": "",
            "sym": symbol.upper(),
            "mode": mode,
            "llm_configured": LLM_CONFIGURED,
        },
    )


@app.get("/research/{symbol}/report", response_class=HTMLResponse)
async def research_report(
    request: Request,
    symbol: str,
    mode: str = "thorough",
    fresh: int = 0,
    profile: Literal["penny", "largecap"] | None = Query(default=None),
):
    sym = symbol.upper()
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
        return templates.TemplateResponse(
            request,
            "partials/research_report.html",
            {
                "result": result,
                "mode": mode,
                "watched": db.has(sym),
                "sizing": sizing,
                "profile_label": result_profile.label,
                "profile_override": profile,
            },
        )
    except BudgetExceededError as e:
        return _error_partial(
            request,
            f"Daily LLM budget reached (${e.spent:.2f} of ${e.limit:.2f}). "
            "Resets at midnight UTC; cached reports still load.",
        )
    except InsufficientDataError:
        logger.exception("research failed: insufficient data for %s", sym)
        return _error_partial(request, f"No market data found for {sym} — check the ticker symbol.")
    except Exception:
        logger.exception("research failed for %s", sym)
        retry_url = f"/research/{sym}/report?mode={mode}&fresh={fresh}"
        if profile:
            retry_url += f"&profile={profile}"
        return _error_partial(request, "Research failed — see server logs.", retry_url)


# --- Watchlist --------------------------------------------------------------


@app.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request):
    return templates.TemplateResponse(
        request, "watchlist.html", {"active": "watchlist", "items": db.list_items()}
    )


@app.post("/watchlist/toggle/{symbol}", response_class=HTMLResponse)
def watchlist_toggle(request: Request, symbol: str):
    sym = symbol.upper()
    watched = db.has(sym)
    if watched:
        db.remove(sym)
    else:
        db.add(sym)
    return templates.TemplateResponse(
        request, "partials/watch_button.html", {"sym": sym, "watched": not watched}
    )


@app.post("/watchlist/remove/{symbol}", response_class=HTMLResponse)
def watchlist_remove(symbol: str):
    db.remove(symbol.upper())
    return HTMLResponse("")  # row is swapped out via hx-swap="outerHTML"


# --- Portfolio --------------------------------------------------------------


@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    valuation = await value_holdings()
    try:
        record_snapshot(valuation)
    except Exception:
        logger.warning("nav snapshot write failed", exc_info=True)
    health = assess_portfolio_health(valuation)
    allocation_slices = _allocation_slices(valuation)
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "active": "portfolio",
            "valuation": valuation,
            "health": health,
            "allocation_slices": allocation_slices,
            "ds_disclaimer": DS_DISCLAIMER,
            "broker_sync_configured": config.SNAPTRADE_CONFIGURED,
            "last_broker_sync": db.get_setting("last_broker_sync"),
            "sync_result": None,
        },
    )


@app.post("/portfolio/broker/sync", response_class=HTMLResponse)
async def portfolio_broker_sync(request: Request):
    try:
        from ..broker.sync import LAST_BROKER_SYNC_KEY, run_sync

        result = await run_sync(dry_run=False)
        response = templates.TemplateResponse(
            request,
            "partials/broker_sync.html",
            {
                "broker_sync_configured": config.SNAPTRADE_CONFIGURED,
                "last_broker_sync": db.get_setting(LAST_BROKER_SYNC_KEY),
                "sync_result": result,
            },
        )
        response.headers["HX-Trigger"] = "txns-changed, holdings-changed"
        return response
    except Exception:
        logger.exception("broker sync failed")
        return _error_partial(request, "Broker sync failed — see server logs.")


@app.get("/portfolio/holdings", response_class=HTMLResponse)
async def portfolio_holdings(request: Request):
    init_holdings_db()
    valuation = await value_holdings()
    return templates.TemplateResponse(
        request,
        "partials/holdings_table.html",
        {"valuation": valuation, "broker_sync_configured": config.SNAPTRADE_CONFIGURED},
    )


async def _transactions_context() -> dict:
    init_holdings_db()
    init_transactions_db()
    valuation = await value_holdings()
    return {
        "returns": compute_returns(valuation),
        "transactions": list_transactions(limit=20),
    }


@app.get("/portfolio/transactions", response_class=HTMLResponse)
async def portfolio_transactions(request: Request):
    try:
        return templates.TemplateResponse(
            request,
            "partials/transactions.html",
            await _transactions_context(),
        )
    except Exception:
        logger.exception("portfolio transactions failed")
        return _error_partial(
            request, "Transactions failed — see server logs.", "/portfolio/transactions"
        )


def _parse_target_form(symbols: list[str], weights_pct: list[str]) -> list[Target]:
    targets: list[Target] = []
    for raw_symbol, raw_weight in zip(symbols, weights_pct):
        symbol = (raw_symbol or "").strip()
        weight = (raw_weight or "").strip()
        if not symbol or not weight:
            continue
        try:
            target_weight = float(weight) / 100
        except ValueError as exc:
            raise ValueError(f"{symbol.upper()} weight must be a number") from exc
        targets.append(Target(symbol=symbol, target_weight=target_weight))
    return targets


def _parse_adopt_form(symbols: list[str], weights: list[str]) -> list[Target]:
    targets: list[Target] = []
    for raw_symbol, raw_weight in zip(symbols, weights):
        symbol = (raw_symbol or "").strip()
        if not symbol:
            continue
        try:
            target_weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{symbol.upper()} weight must be a number") from exc
        targets.append(Target(symbol=symbol, target_weight=target_weight))
    return targets


async def _targets_context() -> dict:
    targets = list_targets()
    valuation = await value_holdings()
    target_map = {target.symbol: target.target_weight for target in targets}
    rows = [
        {"symbol": target.symbol, "weight_pct": target.target_weight * 100}
        for target in targets
    ]
    rows.extend(
        {"symbol": holding.symbol, "weight_pct": None}
        for holding in valuation.holdings
        if holding.symbol not in target_map
    )
    if not rows:
        rows.append({"symbol": "", "weight_pct": None})
    total_target = sum(target_map.values())
    return {
        "targets": targets,
        "rows": rows,
        "implicit_cash_weight": max(0.0, 1.0 - total_target),
    }


@app.get("/portfolio/targets", response_class=HTMLResponse)
async def portfolio_targets(request: Request):
    try:
        return templates.TemplateResponse(
            request, "partials/targets_form.html", await _targets_context()
        )
    except Exception:
        logger.exception("portfolio targets failed")
        return _error_partial(
            request, "Target allocations failed — see server logs.", "/portfolio/targets"
        )


@app.post("/portfolio/targets", response_class=HTMLResponse)
async def portfolio_targets_set(request: Request):
    form = await request.form()
    try:
        targets = _parse_target_form(
            list(form.getlist("symbol[]")),
            list(form.getlist("weight_pct[]")),
        )
        set_targets(targets)
        response = templates.TemplateResponse(
            request, "partials/targets_form.html", await _targets_context()
        )
        response.headers["HX-Trigger"] = "targets-changed"
        return response
    except ValueError as e:
        return _error_partial(request, str(e))
    except Exception:
        logger.exception("portfolio targets update failed")
        return _error_partial(request, "Target update failed — see server logs.")


@app.post("/portfolio/targets/adopt", response_class=HTMLResponse)
async def portfolio_targets_adopt(request: Request):
    form = await request.form()
    try:
        targets = _parse_adopt_form(
            list(form.getlist("symbol[]")),
            list(form.getlist("weight[]")),
        )
        set_targets(targets)
        response = templates.TemplateResponse(
            request, "partials/targets_form.html", await _targets_context()
        )
        response.headers["HX-Trigger"] = "targets-changed"
        return response
    except ValueError as e:
        return _error_partial(request, str(e))
    except Exception:
        logger.exception("portfolio optimizer target adoption failed")
        return _error_partial(request, "Target adoption failed — see server logs.")


@app.get("/portfolio/rebalance", response_class=HTMLResponse)
async def portfolio_rebalance(request: Request):
    try:
        targets = list_targets()
        valuation = await value_holdings()
        plan = plan_rebalance(valuation, targets)
        return templates.TemplateResponse(
            request,
            "partials/rebalance_plan.html",
            {
                "plan": plan,
                "has_targets": bool(targets),
                "ds_disclaimer": DS_DISCLAIMER,
            },
        )
    except Exception:
        logger.exception("portfolio rebalance failed")
        return _error_partial(
            request, "Rebalance plan failed — see server logs.", "/portfolio/rebalance"
        )


@app.post("/portfolio/whatif", response_class=HTMLResponse)
async def portfolio_whatif(request: Request, amount: str = Form("")):
    try:
        contribution = float(amount)
    except (TypeError, ValueError):
        return _error_partial(request, "Contribution amount must be a positive number.")
    if contribution <= 0 or not isfinite(contribution):
        return _error_partial(request, "Contribution amount must be a positive number.")

    try:
        targets = list_targets()
        valuation = await value_holdings()
        plan = plan_contribution(valuation, targets, contribution)
        return templates.TemplateResponse(
            request,
            "partials/whatif.html",
            {
                "plan": plan,
                "has_targets": bool(targets),
                "ds_disclaimer": DS_DISCLAIMER,
            },
        )
    except Exception:
        logger.exception("portfolio what-if failed")
        return _error_partial(request, "Contribution preview failed — see server logs.")


@app.get("/portfolio/nav", response_class=HTMLResponse)
async def portfolio_nav(request: Request):
    try:
        init_holdings_db()
        valuation = await value_holdings()
        series = build_nav_series(list_snapshots())
        chart = nav_area(series.points)
        return templates.TemplateResponse(
            request,
            "partials/nav_history.html",
            {
                "series": series,
                "chart": chart,
                "recent": list(reversed(series.points[-10:])),
                "returns": compute_returns(valuation),
            },
        )
    except Exception:
        logger.exception("portfolio nav history failed")
        return _error_partial(
            request, "Portfolio NAV history failed — see server logs.", "/portfolio/nav"
        )


@app.get("/portfolio/correlation", response_class=HTMLResponse)
async def portfolio_correlation(request: Request):
    try:
        valuation = await value_holdings()
        ordered_holdings = sorted(
            valuation.holdings,
            key=lambda h: h.weight if h.weight is not None else -1.0,
            reverse=True,
        )
        symbols = [h.symbol for h in ordered_holdings]
        insight = await compute_correlation_insight(symbols) if len(symbols) >= 2 else None
        return templates.TemplateResponse(
            request,
            "partials/portfolio_correlation.html",
            {
                "insight": insight,
                "symbols": symbols,
                "too_few": len(symbols) < 2,
                "corr_color": corr_color,
            },
        )
    except Exception:
        logger.exception("portfolio correlation failed")
        return _error_partial(
            request, "Portfolio correlation failed — see server logs.", "/portfolio/correlation"
        )


@app.get("/portfolio/regime", response_class=HTMLResponse)
async def portfolio_regime(request: Request):
    try:
        valuation = await value_holdings()
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        signal = await compute_regime_signal(weights) if weights else None
        return templates.TemplateResponse(
            request,
            "partials/portfolio_regime.html",
            {
                "signal": signal,
                "has_holdings": bool(valuation.holdings),
            },
        )
    except Exception:
        logger.exception("portfolio volatility regime failed")
        return _error_partial(
            request, "Portfolio volatility regime failed — see server logs.", "/portfolio/regime"
        )


@app.get("/portfolio/tax", response_class=HTMLResponse)
async def portfolio_tax(request: Request):
    try:
        valuation = await value_holdings()
        tax_signals = compute_tax_signals(
            valuation,
            list_transactions(limit=10_000),
            datetime.now(UTC).date(),
        )
        return templates.TemplateResponse(
            request,
            "partials/tax_signals.html",
            {"tax_signals": tax_signals},
        )
    except Exception:
        logger.exception("portfolio tax signals failed")
        return _error_partial(
            request, "Tax flags failed — see server logs.", "/portfolio/tax"
        )


@app.get("/portfolio/performance", response_class=HTMLResponse)
async def portfolio_performance(request: Request):
    try:
        valuation = await value_holdings()
        has_holdings = bool(valuation.holdings)
        if not has_holdings:
            return templates.TemplateResponse(
                request,
                "partials/performance.html",
                {"has_holdings": False, "metrics": None, "backtest_caveat": BACKTEST_CAVEAT},
            )
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        metrics = await compute_performance(weights) if weights else None
        return templates.TemplateResponse(
            request,
            "partials/performance.html",
            {"has_holdings": True, "metrics": metrics, "backtest_caveat": BACKTEST_CAVEAT},
        )
    except Exception:
        logger.exception("portfolio performance failed")
        return _error_partial(
            request, "Portfolio performance failed — see server logs.", "/portfolio/performance"
        )


@app.get("/portfolio/twr", response_class=HTMLResponse)
async def portfolio_twr(request: Request):
    try:
        summary = await compute_twr_summary()
        return templates.TemplateResponse(
            request,
            "partials/portfolio_twr.html",
            {"summary": summary},
        )
    except Exception:
        logger.exception("portfolio twr failed")
        return _error_partial(request, "Portfolio TWR failed — see server logs.", "/portfolio/twr")


@app.get("/portfolio/tearsheet", response_class=HTMLResponse)
async def portfolio_tearsheet(request: Request):
    import anyio

    try:
        valuation = await value_holdings()
        if not valuation.holdings:
            return HTMLResponse("<p>No holdings — add positions first.</p>")
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        if not weights:
            return HTMLResponse("<p>Unable to compute weights — prices may be unavailable.</p>")
        html = await anyio.to_thread.run_sync(
            lambda: tearsheet_html(weights)
        )
        if not html:
            return HTMLResponse("<p>Could not generate tearsheet — insufficient price history.</p>")
        return HTMLResponse(content=html)
    except Exception:
        logger.exception("portfolio tearsheet failed")
        return _error_partial(
            request, "Portfolio tearsheet failed — see server logs.", "/portfolio/tearsheet"
        )


@app.post("/portfolio/optimize", response_class=HTMLResponse)
async def portfolio_optimize(request: Request):
    import anyio

    form = await request.form()
    objective = form.get("objective", "max_sharpe")
    excluded_symbols: list[str] = []

    valuation = await value_holdings()
    holdings = []
    for v in valuation.holdings:
        if v.price is not None and v.price < PENNY_PRICE_MAX:
            excluded_symbols.append(v.symbol)
            continue
        if v.market_value:
            holdings.append(Holding(symbol=v.symbol, value=v.market_value))

    exclusion_warnings = _optimizer_exclusion_warnings(excluded_symbols)

    if not holdings:
        reason = OPTIMIZER_EXCLUSION_WARNING if excluded_symbols else "No symbols provided."
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {"available": False, "reason": reason, "warnings": exclusion_warnings},
        )

    # Only exclusions force this skip — a lone holding with nothing excluded keeps
    # the optimizer's existing single-asset (100%) path.
    if excluded_symbols and len({h.symbol for h in holdings}) < 2:
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {
                "available": False,
                "reason": OPTIMIZER_EXCLUSION_WARNING,
                "warnings": exclusion_warnings,
            },
        )

    req = OptimizeRequest(holdings=holdings, objective=objective)
    try:
        result = await anyio.to_thread.run_sync(optimize, req)
    except NoDataError as e:
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {"available": False, "reason": str(e), "warnings": exclusion_warnings},
        )
    except Exception as e:
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {
                "available": False,
                "reason": f"Optimization failed: {e}",
                "warnings": exclusion_warnings,
            },
        )
    drift = analyze_drift(result)
    result.warnings = exclusion_warnings + result.warnings
    chart = frontier_chart(result.efficient_frontier, result.optimal, result.current)
    return templates.TemplateResponse(
        request,
        "partials/portfolio_results.html",
        {"available": True, "result": result, "drift": drift, "frontier_chart": chart},
    )
