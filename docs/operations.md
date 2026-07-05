# Operations

## Environment variables

| Var | Required? | Effect | Failure mode when absent |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | **Required** for research/discover | Auth for every LLM call (research, critic, discovery) via OpenRouter. | The web app starts anyway (`LLM_CONFIGURED=False` logs a startup warning and is passed to templates to show a disabled state). The first LLM call raises `RuntimeError("OPENROUTER_API_KEY is not set...")` lazily — web routes catch it as a generic exception and show an error partial; the CLI has no such guard and will print the raw traceback. |
| `DAILY_LLM_BUDGET_USD` | Optional (default `5`; `0` disables) | Stops new paid LLM runs once today's UTC `usage.jsonl` spend is at or above this amount. | Cached research reports still load because the guard only runs inside cache misses. Uncached research and discovery show a budget error in the web app; CLI commands raise the same guard exception. |
| `DISCORD_WEBHOOK_URL` | Optional (default empty) | Enables Discord drift-alert posts from the daily portfolio job when `DRIFT_ALERT_ENABLED=1`. | Empty means alerts are disabled; snapshots still run. |
| `DAILY_JOB_HOUR_UTC` | Optional (default `21`) | UTC hour for the in-process daily portfolio job; `21` runs after the regular US market close. Set `<0` to disable the loop, primarily for tests. | Uses the default hour. If disabled, no automatic snapshot or alert runs until the app is restarted with a non-negative hour. |
| `DRIFT_ALERT_ENABLED` | Optional (default `1`) | Master switch for daily Discord rebalance drift alerts. | Set `0` to suppress alerts while still allowing the daily snapshot job to run. |
| `FINNHUB_API_KEY` | Optional | Preferred quote/news source over Yahoo when set. | Finnhub calls are skipped entirely (not attempted, not marked `error` — they simply produce no `sources` entry). Quote/news fall back to Yahoo. |
| `FRED_API_KEY` | Optional | Enables macro context (fed funds, CPI YoY, 10y treasury, unemployment, GDP growth). | `sources.macro = "disabled"`; `TickerData.macro` stays `None`; the report has no macro section rather than an empty one. |
| `SEC_IDENTITY` | Optional | Contact email SEC EDGAR requires for XBRL financials. | Falls back to a hardcoded address in `app/data/sec.py`; financials still fetch normally — set your own for anything beyond local use. |
| `WORKHORSE_MODEL` | Optional (default `google/gemini-3.1-flash-lite`) | Model for the research draft, discovery, and the cheap-mode critic. | Uses the default. |
| `PREMIUM_MODEL` | Optional (default `anthropic/claude-sonnet-5`) | Model for the thorough-mode critic/revise chain. | Uses the default. |
| `STOCKS_DB_PATH` | Optional (default `<repo>/stocks.db`) | SQLite file for the watchlist, holdings, settings, targets, NAV snapshots, and transactions tables. | Uses the default path. |
| `STOCKS_CACHE_DIR` | Optional (default `<repo>/.cache`) | Root of every file cache namespace, plus `usage.jsonl`. | Uses the default path. |
| `LOG_LEVEL` | Optional (default `INFO`) | Root logger level — **web app only**; `app/cli.py` never calls `logging.basicConfig`. | Web app defaults to `INFO`; the CLI runs under Python's default root logger (effectively `WARNING`) regardless of this var. |

`.env` is auto-loaded once at import of `app/config.py` (via `python-dotenv`,
which ships with `fastapi[standard]`), so both the web app and the CLI see the
same values from one file.

## Running

**Local dev:**
```bash
uv sync
uv run uvicorn app.web.app:app --reload --port 8000
```

**CLI:**
```bash
uv run stocks research AAPL [--cheap] [--fresh]
uv run stocks discover "<goal>"
uv run stocks usage
```

**Docker:**
```bash
docker build -t stocks . && docker run -p 8000:8000 --env-file .env -v stocks-data:/data stocks
```
The image sets `STOCKS_DB_PATH=/data/stocks.db` and `STOCKS_CACHE_DIR=/data/.cache`
and declares `/data` as a volume. **Without `-v`, every container
restart/recreate starts from empty**: no watchlist, no holdings, no transactions, no caches —
the next request re-fetches and re-researches everything from scratch (and,
for research, re-pays the LLM cost for anything requested that trading day).

**Log verbosity** (web app only):
```bash
LOG_LEVEL=DEBUG uv run uvicorn app.web.app:app --reload --port 8000
```

## Portfolio CSV import

The portfolio page accepts holdings CSV uploads up to 100 KB and 500 data rows.
The header row is required; `symbol` and `shares` are required columns,
`avg_cost` is optional, and extra columns are ignored. Headers are
case-insensitive and UTF-8 BOMs are tolerated.

```csv
symbol,shares,avg_cost
AAPL,10,150.25
MSFT,4,
```

Symbols are uppercased before saving. `shares` must be a positive number;
blank or unparseable `avg_cost` values are saved as empty. Bad data rows are
reported with line numbers and do not block valid rows from importing.

## Transaction CSV import

The transactions panel accepts CSV uploads with the same 100 KB file limit,
500 data-row limit, UTF-8/UTF-8-BOM decoding, and case-insensitive headers as
holdings import. The header must include `date` and `side`; these columns are
recognized when present:

```csv
date,side,symbol,shares,price,amount,note
2026-01-01,deposit,,,,10000,initial cash
2026-01-02,buy,AAPL,10,150,,first lot
2026-02-01,sell,AAPL,2,175,,trim
2026-03-01,withdraw,,,,500,cash out
```

Rows are applied in file order, so put them oldest first. Buy/sell rows ignore
the CSV `amount` value and compute amount from `shares * price`; deposit and
withdraw rows require `amount`. Each valid row mutates recorded cash and/or the
authoritative holdings table immediately. Bad rows are reported with line
numbers and do not block later rows. Deleting a transaction later removes only
the ledger record, not its cash or holdings effect.

## Daily portfolio job and alerts

The web process starts one in-process `asyncio` daily job from the FastAPI
lifespan. There is no sidecar service: the app must be running at
`DAILY_JOB_HOUR_UTC` for that day's automatic snapshot and alert check to
happen. The job records the same NAV snapshot the portfolio page records, so
the snapshot rule is unchanged: skip partially-priced or zero-value valuations.

Discord drift alerts are deterministic rebalance-plan messages. They run only
when `DRIFT_ALERT_ENABLED=1` and `DISCORD_WEBHOOK_URL` is non-empty, and they
dedupe on the current actionable symbol set so repeated daily checks do not
spam the same drift. A new symbol crossing into the actionable set sends a new
alert. Webhook post failures are logged and do not update the dedupe key.

To configure Discord, create an incoming webhook for the target channel and
copy its URL into `.env` as `DISCORD_WEBHOOK_URL`. Discord's UI walkthrough is:
<https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks>.

## Portfolio visuals

Portfolio charts are server-rendered SVG/CSS, not client-side chart libraries.
The allocation donut is computed from priced holdings by market value plus a
cash slice when cash is positive; unpriced holdings are excluded and named
under the legend. The NAV panel needs at least two stored daily snapshots before
it can draw its filled area chart.

The correlation heatmap depends on the `correlation` cache. If a cached
correlation insight was created before matrix support was added, the narrative
and high-pair list still render, and the heatmap appears automatically after
that cache entry expires and recomputes (24-hour TTL).

## Cost & usage

`DAILY_LLM_BUDGET_USD` is the daily cost control for personal use. The guard
sums `totals.cost_usd` from usage events whose timestamps start with today's
UTC date and checks that total before a new LLM run starts. A run that crosses
the limit mid-flight is allowed to finish and record usage; the next uncached
research or discovery request is blocked until UTC midnight. Cached research
reports bypass the guard entirely and continue to load at zero LLM cost.

Every research/discover run appends one JSON line to `.cache/usage.jsonl`
(`app/llm/usage.py::_emit`):

```jsonc
{
  "ts": "2026-07-04T12:00:00+00:00",
  "kind": "research",              // or "discover"
  "subject": "AAPL",                // symbol, or the discovery goal text
  "mode": "thorough",               // "thorough" | "cheap"; discover runs are tagged "thorough" too — there's no real mode concept for discover, it's just the RunContext default
  "cached": false,                  // true only on a report-cache hit — zero LLM calls happened
  "calls": [
    {
      "call_site": "research",      // one of: research, critique, revise, re-critique, discover-plan, discover-rationale
      "model": "google/gemini-3.5-flash",
      "input_tokens": 1234,
      "output_tokens": 567,
      "cached_input_tokens": 0,     // tokens served from the OpenRouter/Anthropic prompt cache — billed at a reduced rate
      "cost_usd": 0.00041,          // real $ from OpenRouter usage accounting; 0.0 if the provider didn't report it
      "duration_ms": 812
    }
    // ...one entry per LLM call in the chain
  ],
  "totals": { "calls": 2, "input_tokens": 2345, "output_tokens": 890, "cached_input_tokens": 1200, "cost_usd": 0.0019, "duration_ms": 1500 },
  "python": "3.13.1",
  "revised": false                  // present for "research" kind only
}
```

The **same event** is also pretty-printed to stderr right after the run
(`format_event`), both in the CLI and in the web app's server log:

```
[usage] research AAPL · mode=thorough
  research          google/gemini-3.5-flash     in=1801 (cached 0) out=612 $0.00034 743ms
  critique          anthropic/claude-sonnet-4.6 in=2450 (cached 1801) out=310 $0.00891 1120ms
  TOTAL  calls=2 in=4251 (cached 1801) out=922 $0.00925 1863ms
```

Read it as: `cost_usd` per call and in `TOTAL` is the actual dollar charge;
`cached` next to `in=` is how many of those input tokens hit the prompt cache
(high numbers on `critique`/`revise`/`re-critique` mean the shared ground-truth
prefix — methodology §5 — is doing its job). `stocks usage` (`format_rollup`)
prints the last 20 events plus an all-time rollup: total cost, total input
tokens, cache-hit percentage, and total output tokens across every run ever
logged.

## Troubleshooting

**Error panels** ("X failed — see server logs") never show a stack trace by
design — the real error is logged server-side via `logger.exception`. Check
the terminal running `uvicorn` (or `docker logs <container>`) for the actual
traceback.

**"No market data found for {symbol}"** is a specific case (research only):
every source came back with neither a usable quote nor a market cap. Almost
always an invalid or delisted ticker; occasionally a simultaneous Yahoo +
Finnhub outage.

**Source-status chips** — `ok` (fetched, usable), `empty` (fetched, nothing
usable), `error` (the fetch raised; a safe fallback was substituted), `disabled`
(never attempted — that source's API key is unset). These are the actual
completeness signal; check them before trusting a report's prose tone.

**Why a report shows lower confidence than expected**: the displayed
confidence is `min()` of three independent inputs — what the report itself
claims, the critic's `suggested_confidence`, and a completeness-weighted
`computed` grade (methodology §6) that's further hard-capped to `medium` if
any source errored, or `low` if there's no quote at all. If it's lower than
you expected, check `critique.suggested_confidence` and
`confidence_assessment.reasons` (visible in the critic-review panel) — one of
those, not the report's own optimism, pulled it down.

**Force-refresh** — `fresh=1` on `/research/{symbol}/report`, or `--fresh` on
`stocks research`, bypasses the *read* side of every cache touched by that
request: `data` (15 min), `sec` (24h), `macro` (6h), `scorecard` (24h), and
`report` (24h) all re-fetch/re-run instead of returning a cached value, then
write the new result back (refreshing each TTL). It does **not** touch the
portfolio `correlation` cache (that panel has no fresh param) or the
watchlist/holdings/transactions SQLite tables.

**Cache locations** — everything lives under `STOCKS_CACHE_DIR` (default
`.cache/`) as `<namespace>/<key>.json`, plus `usage.jsonl` at its root.
`data/`, `sec/`, `macro/`, `report/`, `scorecard/`, and `correlation/` are all
safe to delete any time — they're pure TTL caches; the only cost of clearing
one is a paid re-fetch or re-research next time it's needed. The SQLite file
at `STOCKS_DB_PATH` (default `stocks.db`) is **not** a cache — it's the
watchlist, holdings, and transaction dataset. Deleting it loses that data
permanently.
