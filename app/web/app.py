"""FastAPI app: server-rendered pages + HTMX partials. Folds in the portfolio
optimizer (formerly an HTTP sidecar) as a direct in-process call."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from csv import Error as CsvError
from csv import reader as csv_reader
from io import StringIO
from math import isfinite
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import config, db
from ..config import OPENROUTER_API_KEY
from ..jobs import daily_loop
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
    plan_contribution,
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
from ..portfolio.transactions import (
    Transaction,
    apply_transaction,
    compute_returns,
    delete_transaction,
    init_transactions_db,
    list_transactions,
)
from ..schemas import Confidence

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

OPTIMIZER_EXCLUSION_WARNING = (
    "excluded from mean-variance optimization: sample statistics on illiquid micro-caps "
    "are unreliable"
)

MAX_IMPORT_BYTES = 100 * 1024
MAX_IMPORT_ROWS = 500


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
    if config.DAILY_JOB_HOUR_UTC >= 0:
        task = asyncio.create_task(daily_loop())
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
    optimizer_rows = [
        {
            "symbol": v.symbol,
            "value": round(v.market_value, 2) if v.market_value is not None else None,
            "price": v.price,
        }
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


@app.get("/portfolio/holdings", response_class=HTMLResponse)
async def portfolio_holdings(request: Request):
    init_holdings_db()
    valuation = await value_holdings()
    return templates.TemplateResponse(
        request, "partials/holdings_table.html", {"valuation": valuation}
    )


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


@app.post("/portfolio/import", response_class=HTMLResponse)
async def portfolio_import(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        return _error_partial(request, "CSV import failed: file must be 100 KB or smaller.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _error_partial(request, "CSV import failed: file must be UTF-8 encoded.")

    try:
        rows = list(csv_reader(StringIO(text)))
    except CsvError:
        return _error_partial(request, "CSV import failed: unable to parse CSV.")

    if not rows:
        return _error_partial(request, "CSV import failed: header row is required.")

    header = [col.strip().lower() for col in rows[0]]
    column_map = {name: index for index, name in enumerate(header) if name}
    if "symbol" not in column_map or "shares" not in column_map:
        return _error_partial(
            request, "CSV import failed: header must include symbol and shares columns."
        )

    data_rows = rows[1:]
    if len(data_rows) > MAX_IMPORT_ROWS:
        return _error_partial(request, "CSV import failed: maximum 500 data rows allowed.")

    imported = 0
    skipped: list[str] = []
    symbol_index = column_map["symbol"]
    shares_index = column_map["shares"]
    avg_cost_index = column_map.get("avg_cost")

    for line_number, row in enumerate(data_rows, start=2):
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

    valuation = await value_holdings()
    return templates.TemplateResponse(
        request,
        "partials/holdings_table.html",
        {
            "valuation": valuation,
            "import_summary": {"imported": imported, "skipped": skipped},
        },
    )


def _parse_optional_form_float(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw)


async def _transactions_context(import_summary: dict | None = None) -> dict:
    init_holdings_db()
    init_transactions_db()
    valuation = await value_holdings()
    return {
        "valuation": valuation,
        "returns": compute_returns(valuation),
        "transactions": list_transactions(limit=20),
        "import_summary": import_summary,
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


@app.post("/portfolio/transactions", response_class=HTMLResponse)
async def portfolio_transaction_add(
    request: Request,
    ts: str = Form(""),
    side: str = Form(""),
    symbol: str = Form(""),
    shares: str = Form(""),
    price: str = Form(""),
    amount: str = Form(""),
    note: str = Form(""),
):
    try:
        side_clean = side.strip().lower()
        parsed_shares = _parse_optional_form_float(shares)
        parsed_price = _parse_optional_form_float(price)
        parsed_amount = (
            float(parsed_shares or 0) * float(parsed_price or 0)
            if side_clean in {"buy", "sell"}
            else float(amount)
        )
        apply_transaction(
            Transaction(
                ts=ts.strip(),
                side=side_clean,
                symbol=symbol.strip() or None,
                shares=parsed_shares,
                price=parsed_price,
                amount=parsed_amount,
                realized_pl=None,
                note=note.strip(),
            )
        )
        response = templates.TemplateResponse(
            request,
            "partials/transactions.html",
            await _transactions_context(),
        )
        response.headers["HX-Trigger"] = "txns-changed, holdings-changed"
        return response
    except (TypeError, ValueError) as e:
        return _error_partial(request, str(e))
    except Exception:
        logger.exception("portfolio transaction add failed")
        return _error_partial(request, "Transaction failed — see server logs.")


@app.post("/portfolio/transactions/delete/{txn_id}", response_class=HTMLResponse)
async def portfolio_transaction_delete(request: Request, txn_id: int):
    try:
        delete_transaction(txn_id)
        response = templates.TemplateResponse(
            request,
            "partials/transactions.html",
            await _transactions_context(),
        )
        response.headers["HX-Trigger"] = "txns-changed"
        return response
    except Exception:
        logger.exception("portfolio transaction delete failed")
        return _error_partial(request, "Transaction delete failed — see server logs.")


@app.post("/portfolio/transactions/import", response_class=HTMLResponse)
async def portfolio_transactions_import(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        return _error_partial(request, "CSV import failed: file must be 100 KB or smaller.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _error_partial(request, "CSV import failed: file must be UTF-8 encoded.")

    try:
        rows = list(csv_reader(StringIO(text)))
    except CsvError:
        return _error_partial(request, "CSV import failed: unable to parse CSV.")

    if not rows:
        return _error_partial(request, "CSV import failed: header row is required.")

    header = [col.strip().lower() for col in rows[0]]
    column_map = {name: index for index, name in enumerate(header) if name}
    if "date" not in column_map or "side" not in column_map:
        return _error_partial(request, "CSV import failed: header must include date and side.")

    data_rows = rows[1:]
    if len(data_rows) > MAX_IMPORT_ROWS:
        return _error_partial(request, "CSV import failed: maximum 500 data rows allowed.")

    def cell(row: list[str], name: str) -> str:
        index = column_map.get(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    imported = 0
    skipped: list[str] = []
    for line_number, row in enumerate(data_rows, start=2):
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

    response = templates.TemplateResponse(
        request,
        "partials/transactions.html",
        await _transactions_context({"imported": imported, "skipped": skipped}),
    )
    response.headers["HX-Trigger"] = "txns-changed, holdings-changed"
    return response


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
        return templates.TemplateResponse(
            request,
            "partials/nav_history.html",
            {
                "series": series,
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
    prices = form.getlist("price")
    objective = form.get("objective", "max_sharpe")
    excluded_symbols: list[str] = []

    # If no symbols passed via form, seed from holdings
    if not any((s or "").strip() for s in symbols):
        valuation = await value_holdings()
        holdings = []
        for v in valuation.holdings:
            if v.price is not None and v.price < PENNY_PRICE_MAX:
                excluded_symbols.append(v.symbol)
                continue
            if v.market_value:
                holdings.append(Holding(symbol=v.symbol, value=v.market_value))
    else:
        holdings: list[Holding] = []
        for index, (sym, val) in enumerate(zip(symbols, values)):
            sym = (sym or "").strip().upper()
            if not sym:
                continue
            raw_price = prices[index] if index < len(prices) else ""
            price = float(raw_price) if raw_price and raw_price.strip() else None
            if price is not None and price < PENNY_PRICE_MAX:
                excluded_symbols.append(sym)
                continue
            value = float(val) if val and val.strip() else None
            holdings.append(Holding(symbol=sym, value=value))

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
    return templates.TemplateResponse(
        request,
        "partials/portfolio_results.html",
        {"available": True, "result": result, "drift": drift},
    )
