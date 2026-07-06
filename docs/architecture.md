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
`transactions.py` owns the optional SQLite-backed transaction ledger, applies
validated buys/sells/deposits/withdrawals to authoritative holdings and cash,
and computes realized P/L rollups plus money-weighted return.
`snapshots.py` owns the SQLite-backed daily NAV history and the pure NAV series
points/deltas used by the equity-curve partial.
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

**`app/broker/`** — Optional read-only broker sync through SnapTrade.
`snaptrade.py` is the only module that imports the generated SDK and adapts
account-level positions, balances, and activities into local Pydantic models.
`reconcile.py` is pure deterministic diffing/mapping code. `sync.py`
orchestrates fetch, ledger-only activity import, holdings/cash mirror, and the
`last_broker_sync` setting; failures raise so the scheduler retries later.

**`app/web/`** — The FastAPI JSON API. `app.py` owns the application shell,
lifespan scheduler startup/shutdown, DB/table initialization, exception-handler
registration, and inclusion of `api.py`'s `/api` router. `api.py` owns the SPA
contract: Pydantic request/response models and thin route composition over
`data`/`llm`/`indicators`/`portfolio`. It returns raw JSON data for the React
frontend to render and does not own domain logic.

**`frontend/`** — The React SPA (Vite + TanStack tooling) deployed as static
assets on a Cloudflare Worker at `stonks.ikusner.dev`. The Worker proxies
`/api/*` to `stonks-api.ikusner.dev` with a Cloudflare Access service token;
that API hostname terminates at Cloudflare Tunnel and then the FastAPI JSON API
on the VPS.

**`app/jobs.py`** — The in-process background job registry and tick scheduler
started from the FastAPI lifespan. It stores per-job `last_run:*` ledger values
in SQLite `settings`, runs a startup catch-up tick, optionally runs the
SnapTrade broker sync before the daily portfolio job, records a NAV snapshot
from `value_holdings()`, and optionally sends deterministic Discord rebalance
drift alerts. It imports `app.config`, `app.db`, and `app.portfolio`; it has no
LLM dependency and never writes the drift-alert dedupe key when a webhook post
fails.

**`app/alerts.py`** — Deterministic Discord alerts for the user's held plus
watchlisted symbols. It owns the `price_ranges` rolling 52-week range state and
the shared `alerts_sent` idempotency ledger, refreshes ranges with
`portfolio.history.fetch_price_history`, checks live Finnhub quotes for daily
price moves and new highs/lows, and checks Finnhub's earnings calendar for
upcoming earnings dates. It imports no LLM code; failed market-data sources are
logged and skipped, and webhook failures do not mark alerts as sent.

**`app/cache.py`** — The one dependency-light file-cache primitive
(`read_cache`/`write_cache`/`with_cache`) every namespace in the caching table
is built on.
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

### (a) `GET /api/research/{symbol}`

1. `mode` normalizes to `"cheap"` or `"thorough"` (anything else defaults to
   thorough). A `with_run("research", sym, mode)` context opens — usage
   tracking for every LLM call inside this request.
2. `research_ticker_cached(sym, mode, fresh)`:
   - **Cache check** — namespace `report`, key `SYMBOL:YYYY-MM-DD:mode`, TTL
     24h. A hit returns immediately with **zero** network or LLM calls
     (`annotate_run(cached=True, ...)` is the only side effect).
   - On a miss: `fetch_ticker_data` (its own `data`-namespace cache; see the
     caching table) fans out to Yahoo/Finnhub/SEC/FRED concurrently via
     `asyncio.gather`.
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
6. Returns `ResearchResponse` JSON with the full computed `ResearchResult`,
   position-sizing guidance, watchlist state, profile label, and effective
   confidence. The React SPA decides how to render those fields.
7. `InsufficientDataError` → `404` with `{"detail":{"code":"insufficient_data",
   "message":"..."}}`. Budget exhaustion → `429` with `code:
   "budget_exceeded"`. Other failures are logged with traceback and returned as
   `500` JSON using `code: "internal"`.

### (b) Portfolio API endpoints

The React SPA loads portfolio state from independent JSON endpoints under
`/api/portfolio*`. `GET /api/portfolio` recomputes `value_holdings()` (each
holding price is still subject to the 15-minute `data` cache),
opportunistically records a daily NAV snapshot, computes portfolio health, raw
allocation slices, optimizer seed rows, broker-sync state, and the decision
support disclaimer. Snapshot writes are wrapped in `try/except` with a warning
log, so history storage cannot break the portfolio response. The daily job can
also record that UTC day's NAV without a page visit.

- **Holdings and cash** — `GET /api/portfolio/holdings` returns live valuation.
  `POST /api/portfolio/broker/sync` runs SnapTrade sync, mirrors holdings/cash,
  and returns the sync diff plus last-sync state.
- **Transactions** — `GET /api/portfolio/transactions` computes current
  valuation, `ReturnsSummary`, and the last 20 broker-imported ledger rows.
- **Targets and rebalance** — `GET /api/portfolio/targets` returns saved target
  rows, held symbols without targets, and the implicit cash weight. `PUT
  /api/portfolio/targets` fully replaces target rows after validation.
  `GET /api/portfolio/rebalance` recomputes valuation and calls
  `plan_rebalance()`. `POST /api/portfolio/whatif` validates a positive amount
  and returns the buy-only `plan_contribution()` result.
- **NAV and returns** — `GET /api/portfolio/nav` reads recent daily snapshots,
  builds `NavSeries`, recomputes current valuation, and includes
  `ReturnsSummary` for the same payload. Chart geometry is a frontend concern.
- **Correlation and regime** — `GET /api/portfolio/correlation` orders symbols
  by current portfolio weight, calls `compute_correlation_insight` when at
  least two holdings exist, and returns the raw insight including matrix data.
  `GET /api/portfolio/regime` returns the volatility regime signal from current
  weights.
- **Performance and TWR** — `GET /api/portfolio/performance` computes allocation
  backtest metrics from current weights and returns `BACKTEST_CAVEAT`.
  `GET /api/portfolio/twr` returns the time-weighted return summary.
- **Tax and optimizer** — `GET /api/portfolio/tax` returns deterministic tax
  signals over current valuation and ledger history. `POST
  /api/portfolio/optimize` accepts holdings/objective JSON, excludes illiquid
  micro-caps from mean-variance optimization, runs `optimize()` in a worker
  thread, and returns optimizer output plus deterministic drift analysis.

Portfolio API routes log full tracebacks for unexpected failures and return the
standard JSON error envelope, so the SPA can handle panel-level failures without
the API returning HTML.

### (c) Background jobs

`app.web.app` owns the FastAPI lifespan. On startup it calls `build_jobs()` and
creates one `asyncio.create_task(scheduler_loop(jobs_registry))` when the
registry is non-empty; tests can set `DAILY_JOB_HOUR_UTC` below zero to suppress
the current daily job. On shutdown the task is cancelled and awaited.

`scheduler_loop()` runs `run_due_jobs()` immediately on entry for startup
catch-up, then ticks every `SCHEDULER_TICK_SECONDS`. Each job stores its
successful run time in the SQLite `settings` key `last_run:<job name>`.
Pinned-hour jobs that missed one or more days run once at the next tick after
their UTC hour; cadence jobs run when their interval has elapsed. A failed job
logs the exception, does not advance its ledger entry, and does not stop later
jobs in the registry or kill the loop. `run_daily_jobs()` first calls
`value_holdings()` and `record_snapshot()`. If valuation or snapshot recording
raises, it logs and returns `{"snapshot": False, "alert": ""}` without trying
to alert.

Drift alerts run only when both `DRIFT_ALERT_ENABLED` and
`DISCORD_WEBHOOK_URL` are set. The job builds `plan_rebalance(valuation,
list_targets())`, keeps non-hold items, and skips empty plans. Dedupe is the
SQLite `settings` key `last_drift_alert`, stored as
`YYYY-MM-DD:<sorted actionable symbols csv>`; if the stored symbol set matches
the current actionable symbol set, the job skips regardless of date. Otherwise
it posts `{"content": message}` to Discord with `httpx.AsyncClient(timeout=5)`.
Webhook failures are logged and do not update the dedupe key.

SEC filing alerts run only when both `SEC_ALERTS_ENABLED` and
`DISCORD_WEBHOOK_URL` are set. The cadence is `SEC_ALERT_HOURS`; each run
combines watchlist and holdings symbols, resolves them through SEC's ticker-CIK
map, and looks back `SEC_LOOKBACK_DAYS`. Company submissions JSON covers
`SEC_ALERT_FORMS` such as 8-K, 10-Q, and 10-K. SEC EFTS full-text search covers
SC 13D/13G ownership filings because those forms are filed by the holder while
EFTS indexes them under the subject CIK too. Alerts are batched into one Discord
post and deduped globally by accession in the `alerts_sent` table with
`kind='sec_filing'`; rows are inserted only after a successful webhook post.
Ticker-map failure returns early so the next run retries; per-symbol company
filing failures skip only that symbol, and ownership search failures are
best-effort.

Price and earnings alerts are registered when both `ALERTS_ENABLED` and
`DISCORD_WEBHOOK_URL` are set. `price_alerts` refreshes 52-week ranges from
yfinance history, then checks Finnhub quotes for daily moves at or above
`PRICE_MOVE_ALERT_PCT` and for new highs/lows against the stored range.
`earnings_alerts` checks Finnhub's earnings calendar over
`EARNINGS_ALERT_DAYS`. Both jobs send at most one Discord message per run,
dedupe each deterministic alert in `alerts_sent`, and mark ledger rows only
after a successful webhook post; missing API access, per-symbol fetch failures,
and unavailable earnings calendar responses degrade to skipped alerts rather
than a dead scheduler.

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
| `sec_tickers` | `"all"` | 24 h | The SEC ticker-CIK map producer raises on request failures, unexpected shapes, or an empty parsed map, so failures are never persisted. |
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
| `settings` | `app/db.py` | Single-user key/value settings, currently including recorded cash and the drift-alert dedupe key. |
| `holdings` | `app/portfolio/holdings.py` | Current position rows keyed by symbol: shares and optional average cost. |
| `targets` | `app/portfolio/plan.py` | User-owned target allocation rows keyed by symbol; weights are stored as fractions. |
| `nav_snapshots` | `app/portfolio/snapshots.py` | One NAV row per UTC day from the portfolio API or daily job: securities value, cash, total NAV, cost, and unrealized P&L. |
| `transactions` | `app/portfolio/transactions.py` | Broker-mirrored ledger rows: date, side, symbol, shares, price, amount, realized P/L, note, optional broker `external_id`, and creation timestamp. Broker-imported rows are ledger-only and deduped by `external_id`. |
| `price_ranges` | `app/alerts.py` | Rolling 52-week high/low state per symbol, refreshed from price history and updated when live quotes break the stored range. |
| `alerts_sent` | `app/alerts.py` | Shared idempotency ledger keyed by alert kind and deterministic `dedupe_key`; SEC filing alerts use accession numbers as globally unique keys, and webhook failures leave the ledger unwritten for retry. |

## Error-handling conventions

- **JSON error envelope.** API routes return errors as
  `{"detail":{"code":"...","message":"..."}}`. Expected validation failures use
  `400` with `code: "invalid_input"`, LLM budget exhaustion uses `429` with
  `code: "budget_exceeded"`, insufficient research data uses `404` with
  `code: "insufficient_data"`, and unexpected API exceptions use `500` with
  `code: "internal"` after logging the full traceback. `POST
  /api/portfolio/optimize` keeps optimization failures in a successful
  `OptimizeResponse` (`available: false`, `reason`, `warnings`) when the
  optimizer itself cannot produce a portfolio.
- **Source-status capture.** Every external fetch in `app/data/` is wrapped by
  `_capture`, which turns an exception into a safe fallback value plus an
  `error` status entry, rather than letting it propagate — so one dead source
  degrades the merged `TickerData` instead of failing the whole request. This
  status list is what feeds the confidence hard-caps (methodology §6) and the
  UI's source chips.
- **Logging setup.** `logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))`
  is called once, at import time of `app/web/app.py` — so it only takes effect
  when running the web server. The CLI (`app/cli.py`) never calls
  `basicConfig`; it relies on Python's default root logger (effectively
  WARNING-level, no explicit handler), so `logger.warning`/`.exception` calls
  made during a CLI run are far less visible than during a web request unless
  the caller configures logging themselves.

## Concurrency rules

- FastAPI route handlers are a mix of `def` and `async def`. Lightweight
  synchronous API handlers such as `GET /api/meta` and watchlist updates are
  dispatched to Starlette's worker threadpool automatically. Handlers that call
  into async pipelines or portfolio valuation (`discover`, `research`,
  portfolio holdings/correlation/performance/optimize routes) are `async def`
  and run directly on the event loop.
- Blocking libraries are explicitly offloaded, but via two different
  mechanisms depending on where the call lives: `asyncio.to_thread(...)` inside
  `app/data/*`, `app/indicators/engine.py`, `app/portfolio/performance.py`
  (`compute_performance`), and `app/portfolio/decision_support.py`
  (`compute_correlation_insight`) — all the `yfinance`/`edgartools`/`fredapi`/
  `quantstats_lumi` calls — versus `anyio.to_thread.run_sync(...)` inside
  `app/web/api.py` for `optimize()`. Both correctly move blocking work off the
  event loop; the two APIs are simply not unified.
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
