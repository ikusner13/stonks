"""FastAPI app: server-rendered pages + HTMX partials. Folds in the portfolio
optimizer (formerly an HTTP sidecar) as a direct in-process call."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import OPENROUTER_API_KEY
from ..llm.budget import BudgetExceededError
from ..llm.discovery import discover_ideas
from ..llm.pipeline import InsufficientDataError, research_ticker_cached
from ..llm.usage import format_event, with_run
from ..schemas import Confidence
from ..portfolio.decision_support import (
    DISCLAIMER as DS_DISCLAIMER,
)
from ..portfolio.decision_support import (
    analyze_drift,
    assess_portfolio_health,
    compute_correlation_insight,
    suggest_position_size,
)
from ..portfolio.holdings import (
    init_holdings_db,
    list_holdings,
    remove_holding,
    upsert_holding,
    value_holdings,
)
from ..portfolio.optimize import Holding, NoDataError, OptimizeRequest, optimize
from ..portfolio.plan import (
    Target,
    init_targets_db,
    list_targets,
    plan_rebalance,
    set_targets,
)
from ..portfolio.performance import compute_performance, tearsheet_html
from ..portfolio.snapshots import (
    build_nav_series,
    init_snapshots_db,
    list_snapshots,
    record_snapshot,
)

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
LLM_CONFIGURED = bool(OPENROUTER_API_KEY)
if not LLM_CONFIGURED:
    logger.warning("OPENROUTER_API_KEY not set — research and discovery will fail")


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


def _effective_confidence(report: Confidence, critic: Confidence) -> Confidence:
    return min(report, critic, key=_CONF_ORDER.__getitem__)


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

app = FastAPI(title="Stock Research")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

db.init_db()  # idempotent; ensures the watchlist table exists before first request
init_holdings_db()  # idempotent; ensures the holdings table exists before first request
init_snapshots_db()  # idempotent; ensures the NAV history table exists before first request
init_targets_db()  # idempotent; ensures target allocations exist before first request


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
async def research_report(request: Request, symbol: str, mode: str = "thorough", fresh: int = 0):
    sym = symbol.upper()
    mode = "cheap" if mode == "cheap" else "thorough"
    try:
        async with with_run("research", sym, mode) as ctx:
            result = await research_ticker_cached(sym, mode, fresh=bool(fresh))
        print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
        valuation = await value_holdings()
        effective = _effective_confidence(
            result.report.confidence, result.critique.suggested_confidence
        )
        current_weight = next((h.weight for h in valuation.holdings if h.symbol == sym), None)
        sizing = suggest_position_size(
            valuation.total_with_cash, effective, symbol=sym, current_weight=current_weight
        )
        return templates.TemplateResponse(
            request,
            "partials/research_report.html",
            {"result": result, "mode": mode, "watched": db.has(sym), "sizing": sizing},
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
    optimizer_rows = [
        {"symbol": v.symbol, "value": round(v.market_value, 2) if v.market_value is not None else None}
        for v in valuation.holdings
    ]
    health = assess_portfolio_health(valuation)
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "active": "portfolio",
            "valuation": valuation,
            "optimizer_rows": optimizer_rows,
            "health": health,
            "ds_disclaimer": DS_DISCLAIMER,
        },
    )


@app.get("/portfolio/holdings/row", response_class=HTMLResponse)
def portfolio_holdings_row(request: Request):
    return templates.TemplateResponse(request, "partials/portfolio_row.html", {"r": None})


@app.post("/portfolio/holdings", response_class=HTMLResponse)
async def portfolio_holdings_add(
    request: Request,
    symbol: str = Form(""),
    shares: str = Form(""),
    avg_cost: str = Form(""),
):
    sym = symbol.strip().upper()
    if not sym:
        valuation = await value_holdings()
        return templates.TemplateResponse(
            request, "partials/holdings_table.html", {"valuation": valuation}
        )
    try:
        shares_float = float(shares)
    except (ValueError, TypeError):
        valuation = await value_holdings()
        return templates.TemplateResponse(
            request, "partials/holdings_table.html", {"valuation": valuation}
        )
    avg_cost_float: float | None = None
    if avg_cost and avg_cost.strip():
        try:
            avg_cost_float = float(avg_cost)
        except ValueError:
            pass
    upsert_holding(sym, shares_float, avg_cost_float)
    valuation = await value_holdings()
    return templates.TemplateResponse(
        request, "partials/holdings_table.html", {"valuation": valuation}
    )


@app.post("/portfolio/holdings/remove/{symbol}", response_class=HTMLResponse)
async def portfolio_holdings_remove(request: Request, symbol: str):
    remove_holding(symbol.upper())
    valuation = await value_holdings()
    return templates.TemplateResponse(
        request, "partials/holdings_table.html", {"valuation": valuation}
    )


@app.post("/portfolio/cash", response_class=HTMLResponse)
async def portfolio_cash_set(request: Request, cash: str = Form("")):
    try:
        db.set_cash(float(cash))
    except (TypeError, ValueError):
        pass
    valuation = await value_holdings()
    return templates.TemplateResponse(
        request, "partials/holdings_table.html", {"valuation": valuation}
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


@app.get("/portfolio/nav", response_class=HTMLResponse)
async def portfolio_nav(request: Request):
    try:
        series = build_nav_series(list_snapshots())
        return templates.TemplateResponse(
            request,
            "partials/nav_history.html",
            {"series": series, "recent": list(reversed(series.points[-10:]))},
        )
    except Exception:
        logger.exception("portfolio nav history failed")
        return _error_partial(
            request, "Portfolio NAV history failed — see server logs.", "/portfolio/nav"
        )


@app.get("/portfolio/correlation", response_class=HTMLResponse)
async def portfolio_correlation(request: Request):
    try:
        symbols = [h.symbol for h in list_holdings()]
        insight = await compute_correlation_insight(symbols) if len(symbols) >= 2 else None
        return templates.TemplateResponse(
            request,
            "partials/portfolio_correlation.html",
            {"insight": insight, "too_few": len(symbols) < 2},
        )
    except Exception:
        logger.exception("portfolio correlation failed")
        return _error_partial(
            request, "Portfolio correlation failed — see server logs.", "/portfolio/correlation"
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
                {"has_holdings": False, "metrics": None},
            )
        weights = {h.symbol: h.weight for h in valuation.holdings if h.weight is not None}
        metrics = await compute_performance(weights) if weights else None
        return templates.TemplateResponse(
            request,
            "partials/performance.html",
            {"has_holdings": True, "metrics": metrics},
        )
    except Exception:
        logger.exception("portfolio performance failed")
        return _error_partial(
            request, "Portfolio performance failed — see server logs.", "/portfolio/performance"
        )


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
    symbols = form.getlist("symbol")
    values = form.getlist("value")
    objective = form.get("objective", "max_sharpe")

    # If no symbols passed via form, seed from holdings
    if not any((s or "").strip() for s in symbols):
        valuation = await value_holdings()
        holdings = [
            Holding(symbol=v.symbol, value=v.market_value)
            for v in valuation.holdings
            if v.market_value
        ]
    else:
        holdings: list[Holding] = []
        for sym, val in zip(symbols, values):
            sym = (sym or "").strip().upper()
            if not sym:
                continue
            value = float(val) if val and val.strip() else None
            holdings.append(Holding(symbol=sym, value=value))

    if not holdings:
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {"available": False, "reason": "No symbols provided."},
        )

    req = OptimizeRequest(holdings=holdings, objective=objective)
    try:
        result = await anyio.to_thread.run_sync(optimize, req)
    except NoDataError as e:
        return templates.TemplateResponse(
            request, "partials/portfolio_results.html", {"available": False, "reason": str(e)}
        )
    except Exception as e:
        return templates.TemplateResponse(
            request,
            "partials/portfolio_results.html",
            {"available": False, "reason": f"Optimization failed: {e}"},
        )
    drift = analyze_drift(result)
    return templates.TemplateResponse(
        request,
        "partials/portfolio_results.html",
        {"available": True, "result": result, "drift": drift},
    )
