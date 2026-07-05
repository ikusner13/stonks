# Stock Research

An AI-aided equity research and portfolio decision-support tool. It fetches
real market data, computes a deterministic indicator scorecard in code, and
uses an LLM to turn that ground truth into a readable research report — with a
skeptical critic pass and a programmatic fabrication check to catch invented
numbers. A portfolio page adds holdings and cash tracking, mean-variance
optimization, a historical allocation backtest, and plain-language
decision-support signals (concentration, correlation, drift, position sizing).
An optional transaction ledger can apply deposits, withdrawals, buys, and sells
to the authoritative portfolio state and compute realized P/L plus
money-weighted return.

**What this is not**: it does not place orders, hold custody of money, or give
investment advice. Every number is either fetched from a real source or
computed by code you can read; every LLM output is grounded against that data
and checked before it reaches the page. The human using it makes every trade
decision — the app's job stops at giving that human better-organized evidence.

For how the app decides anything — indicator formulas, the LLM grounding
contract, confidence scoring, portfolio math — see
**[docs/methodology.md](docs/methodology.md)**. For how it's built — module
map, request traces, caching, concurrency — see
**[docs/architecture.md](docs/architecture.md)**. For running it, env vars,
and troubleshooting, see **[docs/operations.md](docs/operations.md)**. For
production hosting (Hetzner + Cloudflare Tunnel/Access), see
**[docs/deployment.md](docs/deployment.md)**.

## Quickstart

```bash
cp .env.example .env   # add OPENROUTER_API_KEY
uv sync
uv run uvicorn app.web.app:app --reload --port 8000
# open http://localhost:8000
```

### Environment keys

| Key | Required? | Unlocks | Degrades to, if unset |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | **Required** | All LLM calls (research, critic, discovery) via OpenRouter. | Research and discovery fail outright; the web app still starts and logs a startup warning. |
| `DAILY_LLM_BUDGET_USD` | Optional | Daily UTC spend ceiling for new LLM runs; default `$5`, set `0` to disable. | Cached research reports still load, but uncached research and discovery are blocked once recorded spend reaches the limit. |
| `DISCORD_WEBHOOK_URL` | Optional | Discord drift alerts from the daily portfolio job. | Alerts are disabled; daily snapshots still run. |
| `DAILY_JOB_HOUR_UTC` | Optional | UTC hour for the in-process daily portfolio job; default `21`, set `<0` to disable. | Uses the default hour. |
| `DRIFT_ALERT_ENABLED` | Optional | Master switch for drift alerts; default `1`. | Set `0` to suppress alerts while keeping daily snapshots. |
| `FINNHUB_API_KEY` | Optional | Real-time US quotes and company news, preferred over Yahoo's when present. | Falls back to Yahoo Finance for quotes and news; the Finnhub source keys are simply absent from `sources` rather than marked `error`. |
| `FRED_API_KEY` | Optional | Macro context (fed funds rate, CPI YoY, 10y treasury, unemployment, GDP growth) injected into the research prompt. | Macro is skipped entirely (`sources.macro = "disabled"`) — the report has no macro section rather than a stale or empty one. |
| `SEC_IDENTITY` | Optional | The contact email SEC EDGAR requires for XBRL financials (revenue, margins, debt, FCF). | Financials still fetch, attributed to a hardcoded fallback address — set your own for real use. |

See [docs/operations.md](docs/operations.md) for the full env var table,
including model overrides and cache/DB paths.

### Running it

```bash
uv run uvicorn app.web.app:app --reload --port 8000   # web app, dev mode
uv run stocks research AAPL                            # CLI: thorough research
uv run stocks research AAPL --cheap                     # CLI: cheap mode
uv run stocks discover "AI infrastructure under $100B market cap"
uv run stocks usage                                     # rolling cost/token summary
```

Docker:

```bash
docker build -t stocks . && docker run -p 8000:8000 --env-file .env -v stocks-data:/data stocks
```

The `-v stocks-data:/data` volume holds the SQLite DB (watchlist, holdings,
transactions) and all file caches. **Without it, every container restart starts with an empty
watchlist and portfolio** — nothing is lost that a re-fetch can't recover, but
your saved holdings, transaction ledger, and watchlist entries are gone for good.

## Feature tour

- **Discover** — describe an investment goal in a sentence; an LLM proposes
  either a predefined Yahoo screen or a list of thematic ticker candidates.
  Every candidate is re-validated against real fetched data before it's shown
  — hallucinated tickers are dropped, and any numeric filter in the goal
  (market cap, P/E) is enforced in code, not trusted from the model.
- **Research** — a full report for one ticker: summary, bull/bear thesis, key
  metrics, an indicator-scorecard read, risks, and open questions.
  - **Profiles**: the app automatically selects `largecap` or `penny` research
    policy from exchange, price, and market cap; override with
    `uv run stocks research SYM --profile penny|largecap` or
    `/research/SYM/report?profile=penny|largecap`.
  - **Thorough mode** (default): workhorse-model draft → premium-model audit →
    if the audit finds fabrication or a medium/high-severity issue, a premium
    revision → a final premium re-critique. Confidence is clamped to the
    lowest of what the report claims, what the critic will accept, and what
    the data completeness supports.
  - **Cheap mode**: workhorse draft, workhorse audit, no revision regardless
    of what the audit finds. Cheaper and faster; the critic is the same model
    that wrote the report, so it's a weaker check than thorough mode's
    independent premium audit.
- **Watchlist** — a server-side (SQLite) list of tracked symbols; toggled from
  any research report, and used to prefill the portfolio page.
- **Portfolio** — holdings valuation, CSV holdings import, dry-powder tracking,
  daily NAV snapshots, optional Discord drift alerts, and an optional
  transactions ledger, plus decision-support panels:
  - **Health**: concentration by top-1/3/5 holding weight, in plain language.
  - **Correlation**: pairwise return correlation flags holdings that move
    together — a source of hidden concentration position weights alone miss.
  - **Allocation backtest**: CAGR/Sharpe/Sortino/volatility/max-drawdown for
    your *current* weights held constant over history, against a benchmark.
  - **Target allocations**: save your own target weights, adopt optimizer
    weights as targets, and generate a deterministic rebalance plan using
    holdings plus recorded cash as the base, including a buy-only contribution
    what-if preview for new cash.
  - **Optimizer**: mean-variance optimal weights (max-Sharpe or min-risk) with
    an efficient frontier, current-vs-optimal drift signals, and confidence-
    scaled position-sizing guidance for new candidates using holdings plus
    recorded cash as the investable base.
  - **Transactions**: deposits, withdrawals, buys, sells, and CSV import apply
    to recorded cash and holdings; the ledger reports realized P/L and
    money-weighted return. Deleting a row removes the record only, not its
    applied effect.

All of the above is deterministic and grounded in fetched or computed data —
never advice, never an order.

## Cost profile

Each research report makes several LLM calls; approximate per-report cost:

- **Cheap mode**: ≈ $0.01 — one workhorse draft, one workhorse audit.
- **Thorough mode**: ≈ $0.06–0.30 — dominated by the premium critic/revise
  chain; revision and re-critique now trigger only on real findings, on top of
  the workhorse draft.

Every LLM call's tokens, cache-read tokens, duration, and real USD cost (from
OpenRouter usage accounting) are appended as one JSON line per run to
`.cache/usage.jsonl`. Run `uv run stocks usage` for a rolling summary, or read
the file directly. `DAILY_LLM_BUDGET_USD` caps new paid runs by UTC day; cached
reports are still served after the cap is reached. See
[docs/operations.md](docs/operations.md) for the event shape.

## Test

```bash
uv run pytest -q
uv run ruff check
```

## Layout

```
app/
  data/        market data (yfinance quotes/fundamentals/news, Finnhub, SEC/EDGAR, FRED, screener)
  indicators/  deterministic indicator scorecard + confidence assessment
  llm/         Pydantic AI pipelines: research, critic, discovery, usage tracking
  portfolio/   holdings valuation, transactions/MWR, NAV snapshots, targets/rebalance, optimizer, backtest, decision_support
  web/         FastAPI app, Jinja2 templates, HTMX partials, static assets
  cache.py     file-based read-through KV (data/sec/macro/report/scorecard/correlation caches)
  db.py        SQLite watchlist/settings store + shared connection helper
  jobs.py      in-process daily NAV snapshot + Discord drift alert loop
  schemas.py   Pydantic models / LLM structured-output contracts
  cli.py       Typer CLI
docs/
  methodology.md   how the app determines stock effectiveness — read before touching indicators or prompts
  architecture.md  module map, request traces, caching, concurrency
  operations.md    env vars, running, cost/usage, troubleshooting
```
