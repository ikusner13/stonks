# Architecture

## Module map

**`app/data/`** — Ticker data acquisition. `__init__.py::fetch_ticker_data` is
the single entry point everything else calls; it fans out to `yahoo.py`
(quotes/fundamentals/news via `yfinance`, always attempted), `finnhub.py`
(quotes/news, only if `FINNHUB_API_KEY` is set), `sec.py` (XBRL financials via
`edgartools`, its own 24h cache), and `macro.py` (FRED series, its own 6h
cache, only if `FRED_API_KEY` is set), then merges them into one `TickerData`
behind a 15-minute cache. `screener.py` is independent — it only backs
Discover's predefined-screen path and isn't part of the merge. Imports
`app.cache`, `app.config`, and `app.schemas`.

**`app/indicators/`** — The deterministic scorecard. `engine.py::compute_scorecard`
fetches its own price history (separately from `app/data`, since it needs raw
`yfinance` OHLC series rather than the summary fields `TickerData` carries) and
computes the 12 indicators in `compute_indicators`. `confidence.py` is pure and
synchronous — it derives a completeness-based confidence grade from a
`TickerData` + `IndicatorScorecard`, with no I/O of its own. `schemas.py` holds
the `Indicator`/`IndicatorScorecard` models. Imports `app.cache` and
`app.schemas`.

**`app/llm/`** — The Pydantic AI pipelines. `provider.py` builds the two model
factories (workhorse/premium) from `app.config`. `research.py` drafts the first
`TickerReport`. `critic.py` runs the audit/revise/re-critique chain and the
programmatic fabrication check, and is the module `pipeline.py`'s
`research_ticker_cached` calls into (report cache, 24h). `discovery.py` is a
separate pipeline (screening plan → validate → rationale) used only by
Discover. `usage.py` is a cross-cutting concern: every agent call in `research.py`/
`critic.py`/`discovery.py` runs through `run_tracked`, which records
tokens/cost/duration into a contextvar-scoped `RunContext`, emitted as one JSON
line to `.cache/usage.jsonl` when the `with_run` context exits. Imports
`app.data`, `app.indicators`, `app.schemas`, `app.config`.

**`app/portfolio/`** — Everything that isn't single-ticker research.
`holdings.py` owns the SQLite-backed positions table and live valuation.
`snapshots.py` owns the SQLite-backed daily NAV history and the server-computed
series model used by the equity-curve partial.
`history.py` is the shared price-history fetch + cleaning helper
(`fetch_price_history`, `drop_short_history`) used by both `performance.py`
(the allocation backtest, via `quantstats_lumi`) and `optimize.py` (mean-variance
optimization via `skfolio`). `plan.py` owns user target allocations and the
deterministic rebalance plan. `decision_support.py` sits on top of these pieces
for portfolio health, optimizer drift, position sizing, and correlation; the
health/drift/sizing helpers are pure over already-computed inputs, while
correlation fetches its own return history and reuses `performance.py`'s
`_fetch_returns` via a local import inside the function body to avoid a
module-level cycle. Imports `app.config`, `app.data`, `app.schemas`.

**`app/web/`** — The FastAPI app (`app.py`), Jinja2 templates, static assets,
and pure SVG/color helpers in `charts.py`. Owns no domain logic — every route
is a thin composition of calls into `data`/`llm`/`indicators`/`portfolio`, plus
the Jinja2 numeric-formatting filters (`fmt_num`, `fmt_cap`, `pct`) and
server-computed chart models for templates to render. Imports nearly everything
else in `app/`.

**`app/cache.py`** — The one dependency-light file-cache primitive
(`read_cache`/`write_cache`/`with_cache`) every namespace in §3 is built on.
No external dependency (no Redis/SQLite) — one JSON file per key under
`.cache/<namespace>/`. Concurrent in-process misses for the same namespace/key
are coalesced with a per-key async lock, so only the first caller runs the
expensive producer and later waiters re-check the file cache before returning.

**`app/db.py`** — The watchlist table (separate from `portfolio/holdings.py`'s
holdings table, though both live in the same SQLite file at `STOCKS_DB_PATH`)
plus the shared SQLite connection helper. Each connection reads
`app.config.DB_PATH` lazily, enables WAL, sets a 5s busy timeout, commits on
success, and closes immediately.

**`app/schemas.py`** — All Pydantic contracts shared across modules
(`TickerData`, `TickerReport`, `Critique`, `ResearchResult`, discovery models).
**Deferred-import pattern**: `TickerData` references `SecFinancials` (from
`app.data.sec`) and `MacroContext` (from `app.data.macro`); `ResearchResult`
references `IndicatorScorecard` (from `app.indicators.schemas`) and
`ConfidenceAssessment` (from `app.indicators.confidence`). Importing any of
these at module load time would cycle back into `app.schemas`, because
`app.indicators.confidence` itself imports `TickerData`/`Confidence` from
`app.schemas` at its own module level. The fix: `app/schemas.py` defines its
classes first, then imports the four dependent types at the *bottom* of the
file (after `# Deferred imports to avoid circular dependency`) and calls
`TickerData.model_rebuild()` / `ResearchResult.model_rebuild()` to resolve the
forward references those imports satisfy.

## Request traces

### (a) `GET /research/{symbol}/report`

1. `mode` normalizes to `"cheap"` or `"thorough"` (anything else defaults to
   thorough). A `with_run("research", sym, mode)` context opens — usage
   tracking for every LLM call inside this request.
2. `research_ticker_cached(sym, mode, fresh)`:
   - **Cache check** — namespace `report`, key `SYMBOL:YYYY-MM-DD:mode`, TTL
     24h. A hit returns immediately with **zero** network or LLM calls
     (`annotate_run(cached=True, ...)` is the only side effect).
   - On a miss: `fetch_ticker_data` (its own `data`-namespace cache, §3) fans
     out to Yahoo/Finnhub/SEC/FRED concurrently via `asyncio.gather`.
   - If there's neither a quote nor a market cap, `InsufficientDataError`
     raises here and propagates **uncached** — the next request retries fresh.
   - `compute_scorecard` (its own `scorecard`-namespace cache) fetches 420
     days of price history + SPY + the earnings calendar date, then computes
     the 12 indicators.
   - `compute_confidence` runs synchronously over the ticker + scorecard
     (no I/O, not cached — cheap enough to recompute every time).
   - `research_ticker_reviewed` runs the LLM chain: draft → audit → (revise →
     re-critique, thorough mode only, conditional on the audit's findings).
   - The report's `confidence` is clamped to
     `min(report.confidence, critique.suggested_confidence, assessment.computed)`.
   - The full `ResearchResult` is cached under `report`.
3. The usage event for this request is formatted and printed to stderr.
4. `value_holdings()` runs again — the function itself has no cache and always
   recomputes, but each holding's price still comes from `fetch_ticker_data`,
   which is subject to the 15-minute `data` cache, so "recomputed" doesn't
   mean the price is always freshly fetched — to find this symbol's current
   portfolio weight, if held.
5. `suggest_position_size` computes a sizing band from the effective
   confidence, `valuation.total_with_cash` (holdings value plus recorded cash),
   and the symbol's current securities-only weight when already held.
6. Renders `partials/research_report.html` with the result, mode, watchlist
   state, and sizing guidance.
7. `InsufficientDataError` → a "no market data found" error partial.
   Any other exception → logged full traceback server-side, generic error
   partial with a retry link that echoes the *original* request's `mode` and
   `fresh` values — it is not forced to `fresh=1`; a request made without
   `fresh` retries the same, cache-eligible way.

### (b) The portfolio page and its HTMX panels

`GET /portfolio` renders the page shell synchronously: `value_holdings()`
(always recomputed — though each holding's price is subject to the 15-minute
`data` cache, so this isn't a guaranteed-fresh network fetch), opportunistically
records a daily NAV snapshot from that valuation, `assess_portfolio_health`
computed inline from that valuation, the allocation donut slices from priced
holdings plus cash, and the holdings table — all present in the initial HTML.
Snapshot writes are wrapped in `try/except` with a warning log, so history
storage can never break page rendering. The remaining panels and forms are
then loaded or submitted independently:

- **A) Holdings, cash, and CSV import** — rendered inline on page load (no HTMX
  round-trip needed for the initial view). Add/remove (`POST /portfolio/holdings`,
  `POST /portfolio/holdings/remove/{symbol}`), cash updates (`POST /portfolio/cash`),
  and CSV import (`POST /portfolio/import`) each re-run `value_holdings()` and
  swap in a fresh `holdings_table` partial.
- **B) Health & correlation** — health and the allocation donut are computed
  inline (part of the initial page render, no fetch of their own). Correlation
  is lazy:
  `hx-get="/portfolio/correlation" hx-trigger="load"` fires immediately after
  page load. The route recomputes live valuation to order symbols by portfolio
  weight descending, calls `compute_correlation_insight` (its own
  `correlation`-cache, §3), and swaps in
  `partials/portfolio_correlation.html`, which renders the narrative, high
  pairs, and a matrix heatmap when the cached insight includes one.
- **C) NAV history** — lazy-loaded on `hx-trigger="load"`:
  `GET /portfolio/nav` reads the last 365 daily snapshots from SQLite, computes
  deltas against the previous and first snapshots, and builds a `NavChart`
  containing a `600x120` polyline, closed area-fill path, first-value baseline,
  and first/last/min/max labels for `partials/nav_history.html`. With fewer
  than two points, no chart is produced.
- **D) Allocation backtest** — also lazy-loaded on `hx-trigger="load"`:
  `GET /portfolio/performance` recomputes `value_holdings()` again (independent
  of the page-load call, same 15-minute price-cache caveat) and calls
  `compute_performance` with the resulting weights.
- **E) Target allocations & rebalance** — `GET /portfolio/targets` lazy-loads
  the editable target-weight form, including held symbols that do not yet have
  target rows and the implicit cash target. `POST /portfolio/targets` fully
  replaces the stored target rows after validation and emits `HX-Trigger:
  targets-changed`. `POST /portfolio/targets/adopt` uses optimizer weights as
  targets. `GET /portfolio/rebalance` listens for page load and that trigger,
  recomputes `value_holdings()`, and calls `plan_rebalance()` using
  `total_with_cash` as the base.
- **F) Optimizer & drift** — the only panel that isn't `hx-trigger="load"`; it
  fires on form submit (`POST /portfolio/optimize`), seeding from the submitted
  rows or, if the form is empty, from current holdings. `optimize()` runs in a
  worker thread (`anyio.to_thread.run_sync`, since `skfolio`/`numpy` there are
  synchronous and CPU-bound), then `analyze_drift` compares the result's
  current-vs-optimal weights and `frontier_chart()` maps the efficient frontier
  plus current/optimal markers into SVG coordinates before rendering
  `partials/portfolio_results.html`.

The independent portfolio panel routes wrap their real work in try/except where
generic failures are expected, logging the full traceback and returning
`partials/error.html` with a retry URL where retrying makes sense — a failed
panel never takes down the rest of the page.

## Caching table

All namespaces share `app/cache.py`'s file-based read-through cache: one JSON
file per key at `.cache/<namespace>/<sanitized-key>.json`, holding
`{"expiresAt": <epoch ms or 0>, "value": <payload>}`.
`with_cache()` also coalesces concurrent in-process misses for the same
namespace/key: waiters serialize behind the first producer, then re-read the
cache so duplicate LLM calls or market-data downloads are avoided when the
first producer succeeds. `fresh=True` skips the initial read but still uses the
same lock, and exceptions still propagate without writing a cache entry.

| Namespace | Key shape | TTL | Negative-caching semantics |
| --- | --- | --- | --- |
| `data` | `SYMBOL` | 15 min | `write_cache` only fires when `produce()` returns non-`None`. `fetch_ticker_data`'s `produce()` always returns a full `TickerData` dict (even with per-source `error` statuses inside it) unless the whole thing raises — so partial failures *are* cached for the TTL; total failures are not cached at all. |
| `sec` | `SYMBOL` | 24 h | Same rule; a `None` result (fetch failed entirely) is never persisted, so the next call retries immediately. |
| `macro` | `"latest"` (single global key, not per-symbol) | 6 h | A `None` result (no `FRED_API_KEY`, or the FRED call raised) is never persisted. |
| `report` | `SYMBOL:YYYY-MM-DD:mode` | 24 h | An `InsufficientDataError` raised inside `produce()` propagates before any write — never cached, always retried. |
| `scorecard` | `SYMBOL:YYYY-MM-DD` | 24 h | `produce()` always returns a scorecard dict (indicators default to `unavailable` rather than raising), so this is effectively always cached once computed. |
| `correlation` | `SYM1-SYM2-...:lookback_days:YYYY-MM-DD` (symbols sorted, joined with `-`) | 24 h | The only namespace whose `produce()` can genuinely return `None` on success (insufficient overlapping history) — that `None` is, by the same universal rule, never persisted, so an under-covered portfolio retries every request until it clears the data threshold rather than being stuck with a cached "no insight" result. Older cached success blobs may lack `matrix`; `CorrelationInsight.matrix` defaults to `None` until the 24h TTL refreshes. |

`fresh=True` (the CLI's `--fresh`, or `?fresh=1` on the research route) bypasses
the read side of `with_cache` unconditionally — it still writes the fresh
result back afterward, refreshing the TTL.

## SQLite storage table

All tables live in the single SQLite database at `STOCKS_DB_PATH` and use
`app/db.py::connect()`.

| Table | Owner | Purpose |
| --- | --- | --- |
| `watchlist` | `app/db.py` | User watchlist symbols plus optional position values. |
| `settings` | `app/db.py` | Single-user key/value settings, currently including recorded cash. |
| `holdings` | `app/portfolio/holdings.py` | Current position rows keyed by symbol: shares and optional average cost. |
| `targets` | `app/portfolio/plan.py` | User-owned target allocation rows keyed by symbol; weights are stored as fractions. |
| `nav_snapshots` | `app/portfolio/snapshots.py` | One opportunistic NAV row per UTC day: securities value, cash, total NAV, cost, and unrealized P&L. |

## Error-handling conventions

- **Error partials over 500s.** Most web routes that do real work (research,
  discover, portfolio import, targets, rebalance, NAV, correlation, performance,
  and tearsheet) catch expected or generic failures and return a rendered
  `partials/error.html` fragment — HTMX swaps this into the panel's target, so
  the rest of the page stays intact. Generic failures in those routes log the
  full traceback via `logger.exception`; validation-style failures return a
  clear message without a stack trace. The user sees a short message ("X failed
  — see server logs") and, where it makes sense, a retry link; never a raw stack
  trace or a bare 500.
  **`POST /portfolio/optimize` is the one exception to this pattern**: it
  catches `NoDataError` and generic `Exception` separately, but neither branch
  calls `logger.exception` (so a failure here leaves no server-side traceback),
  and both render `partials/portfolio_results.html` with `available: False`
  and a `reason` string built from the exception's own text (e.g.
  `f"Optimization failed: {e}"`) — the raw exception message reaches the
  browser directly, and there's no retry URL, unlike every other panel.
- **Source-status capture.** Every external fetch in `app/data/` is wrapped by
  `_capture`, which turns an exception into a safe fallback value plus an
  `error` status entry, rather than letting it propagate — so one dead source
  degrades the merged `TickerData` instead of failing the whole request. This
  status list is what feeds the confidence hard-caps (methodology §4) and the
  UI's source chips.
- **Logging setup.** `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))`
  is called once, at import time of `app/web/app.py` — so it only takes effect
  when running the web server. The CLI (`app/cli.py`) never calls
  `basicConfig`; it relies on Python's default root logger (effectively
  WARNING-level, no explicit handler), so `logger.warning`/`.exception` calls
  made during a CLI run are far less visible than during a web request unless
  the caller configures logging themselves.

## Concurrency rules

- FastAPI route handlers are a mix of `def` and `async def`. Synchronous
  handlers (`index`, `research_page`, `favicon`, `watchlist_page`,
  `watchlist_toggle`, `watchlist_remove`, `portfolio_holdings_row`) are
  dispatched to Starlette's worker threadpool automatically. Handlers that
  call into async pipelines (`discover`, `research_report`, `portfolio_page`,
  the holdings/correlation/performance/tearsheet/optimize routes) are
  `async def` and run directly on the event loop.
- Blocking libraries are explicitly offloaded, but via two different
  mechanisms depending on where the call lives: `asyncio.to_thread(...)` inside
  `app/data/*`, `app/indicators/engine.py`, `app/portfolio/performance.py`
  (`compute_performance`), and `app/portfolio/decision_support.py`
  (`compute_correlation_insight`) — all the `yfinance`/`edgartools`/`fredapi`/
  `quantstats_lumi` calls — versus `anyio.to_thread.run_sync(...)` inside
  `app/web/app.py` for `optimize()` and `tearsheet_html()`. Both correctly move
  blocking work off the event loop; the two APIs are simply not unified.
- Pydantic AI's `agent.run()` calls are natively async (httpx under the hood)
  and need no thread offload.
- **SQLite is connection-per-call, always synchronous, never offloaded to a
  thread.** `app/db.py::connect()` opens a fresh `sqlite3.connect(config.DB_PATH)`
  per function call, configures WAL plus `busy_timeout=5000`, commits, and
  closes — no pooling, no async driver. Several of these calls (e.g.
  `list_holdings()` inside the `async def value_holdings`)
  run inline inside an `async def` without `to_thread`, meaning they briefly
  block the event loop. At this app's actual scale (single user, local SQLite
  file) that's a non-issue in practice, but it is a real inconsistency with
  the to_thread discipline applied everywhere else in the codebase.
