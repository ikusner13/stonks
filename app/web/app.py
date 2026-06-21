"""FastAPI app: server-rendered pages + HTMX partials. Folds in the portfolio
optimizer (formerly an HTTP sidecar) as a direct in-process call."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.discovery import discover_ideas
from ..llm.pipeline import research_ticker_cached
from ..llm.usage import format_event, with_run
from ..portfolio.holdings import (
    init_holdings_db,
    list_holdings,
    remove_holding,
    upsert_holding,
    value_holdings,
)
from ..portfolio.optimize import Holding, NoDataError, OptimizeRequest, optimize
from ..portfolio.performance import compute_performance, tearsheet_html

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")

EXAMPLES = [
    "AI infrastructure under $100B market cap",
    "Undervalued large caps",
    "Growth technology stocks",
    "Most active stocks today",
]


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


templates.env.filters["fmt_num"] = _fmt_num
templates.env.filters["fmt_cap"] = _fmt_cap
templates.env.filters["pct"] = _pct

app = FastAPI(title="Stock Research")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

db.init_db()  # idempotent; ensures the watchlist table exists before first request
init_holdings_db()  # idempotent; ensures the holdings table exists before first request


# --- Discover ---------------------------------------------------------------


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(HERE / "static" / "favicon.ico")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"active": "discover", "examples": EXAMPLES}
    )


@app.post("/discover", response_class=HTMLResponse)
async def discover(request: Request, goal: str = Form("")):
    goal = goal.strip()
    if not goal:
        return HTMLResponse("")
    async with with_run("discover", goal) as ctx:
        result = await discover_ideas(goal)
    print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
    watched = {i.symbol for i in db.list_items()}
    return templates.TemplateResponse(
        request, "partials/candidates.html", {"result": result, "watched_symbols": watched}
    )


# --- Research ---------------------------------------------------------------


@app.get("/research/{symbol}", response_class=HTMLResponse)
def research_page(request: Request, symbol: str, mode: str = "thorough"):
    return templates.TemplateResponse(
        request,
        "research.html",
        {"active": "", "sym": symbol.upper(), "mode": mode},
    )


@app.get("/research/{symbol}/report", response_class=HTMLResponse)
async def research_report(request: Request, symbol: str, mode: str = "thorough", fresh: int = 0):
    sym = symbol.upper()
    mode = "cheap" if mode == "cheap" else "thorough"
    async with with_run("research", sym, mode) as ctx:
        result = await research_ticker_cached(sym, mode, fresh=bool(fresh))
    print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)
    return templates.TemplateResponse(
        request,
        "partials/research_report.html",
        {"result": result, "mode": mode, "watched": db.has(sym)},
    )


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
    holdings = list_holdings()
    optimizer_rows = [{"symbol": h.symbol, "value": None} for h in holdings]
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {"active": "portfolio", "valuation": valuation, "optimizer_rows": optimizer_rows},
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


@app.get("/portfolio/performance", response_class=HTMLResponse)
async def portfolio_performance(request: Request):
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


@app.get("/portfolio/tearsheet", response_class=HTMLResponse)
async def portfolio_tearsheet(request: Request):
    import anyio

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


@app.post("/portfolio/optimize", response_class=HTMLResponse)
async def portfolio_optimize(request: Request):
    import anyio

    form = await request.form()
    symbols = form.getlist("symbol")
    values = form.getlist("value")
    objective = form.get("objective", "max_sharpe")

    # If no symbols passed via form, seed from holdings
    if not any((s or "").strip() for s in symbols):
        current_holdings = list_holdings()
        holdings = [Holding(symbol=h.symbol, value=None) for h in current_holdings]
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
    return templates.TemplateResponse(
        request, "partials/portfolio_results.html", {"available": True, "result": result}
    )
