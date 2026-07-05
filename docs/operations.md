# Operations

## Environment variables

| Var | Required? | Effect | Failure mode when absent |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | **Required** for research/discover | Auth for every LLM call (research, critic, discovery) via OpenRouter. | The web app starts anyway (`LLM_CONFIGURED=False` logs a startup warning and is passed to templates to show a disabled state). The first LLM call raises `RuntimeError("OPENROUTER_API_KEY is not set...")` lazily — web routes catch it as a generic exception and show an error partial; the CLI has no such guard and will print the raw traceback. |
| `FINNHUB_API_KEY` | Optional | Preferred quote/news source over Yahoo when set. | Finnhub calls are skipped entirely (not attempted, not marked `error` — they simply produce no `sources` entry). Quote/news fall back to Yahoo. |
| `FRED_API_KEY` | Optional | Enables macro context (fed funds, CPI YoY, 10y treasury, unemployment, GDP growth). | `sources.macro = "disabled"`; `TickerData.macro` stays `None`; the report has no macro section rather than an empty one. |
| `SEC_IDENTITY` | Optional | Contact email SEC EDGAR requires for XBRL financials. | Falls back to a hardcoded address in `app/data/sec.py`; financials still fetch normally — set your own for anything beyond local use. |
| `WORKHORSE_MODEL` | Optional (default `google/gemini-3.1-flash-lite`) | Model for the research draft, discovery, and the cheap-mode critic. | Uses the default. |
| `PREMIUM_MODEL` | Optional (default `anthropic/claude-sonnet-5`) | Model for the thorough-mode critic/revise chain. | Uses the default. |
| `STOCKS_DB_PATH` | Optional (default `<repo>/stocks.db`) | SQLite file for the watchlist and holdings tables. | Uses the default path. |
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
restart/recreate starts from empty**: no watchlist, no holdings, no caches —
the next request re-fetches and re-researches everything from scratch (and,
for research, re-pays the LLM cost for anything requested that trading day).

**Log verbosity** (web app only):
```bash
LOG_LEVEL=DEBUG uv run uvicorn app.web.app:app --reload --port 8000
```

## Cost & usage

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
prefix — methodology §3 — is doing its job). `stocks usage` (`format_rollup`)
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
`computed` grade (methodology §4) that's further hard-capped to `medium` if
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
watchlist/holdings SQLite tables.

**Cache locations** — everything lives under `STOCKS_CACHE_DIR` (default
`.cache/`) as `<namespace>/<key>.json`, plus `usage.jsonl` at its root.
`data/`, `sec/`, `macro/`, `report/`, `scorecard/`, and `correlation/` are all
safe to delete any time — they're pure TTL caches; the only cost of clearing
one is a paid re-fetch or re-research next time it's needed. The SQLite file
at `STOCKS_DB_PATH` (default `stocks.db`) is **not** a cache — it's the
watchlist and holdings dataset. Deleting it loses that data permanently.
