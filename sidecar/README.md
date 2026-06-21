# Portfolio Sidecar

Optional Python microservice for mean-variance portfolio optimization — the one
Python-shaped feature (Phase 4, W5). Isolated from the core TS app: if it's not
running, the `/portfolio` page degrades to a "sidecar offline" notice.

- **Stack:** FastAPI + [skfolio](https://skfolio.org) (scikit-learn based) + yfinance, managed with [uv](https://docs.astral.sh/uv/), Python 3.13.
- **Data:** fetches its own daily adjusted-close history via yfinance (same vendor as the core app's yahoo-finance2).
- **Output:** mean-variance weights, annualized return/volatility/Sharpe, an efficient-frontier sample. Research context, **not advice**.

## Run (Docker — no host installs)

```bash
cd sidecar
docker compose up --build      # serves on http://localhost:8000
```

`GET /health` → `{"status":"ok"}`. Interactive API docs at `/docs`.

## API

`POST /optimize`

```jsonc
{
  "holdings": [
    { "symbol": "AAPL", "value": 5000 },   // value/shares optional → enables current-vs-optimal
    { "symbol": "MSFT" }
  ],
  "objective": "max_sharpe",                // or "min_risk"
  "lookback_days": 730,                     // 60–3650
  "frontier_points": 20                     // 0 to skip the frontier
}
```

Returns `{ optimal, current?, efficient_frontier, symbols, warnings, disclaimer }`.
Symbols with no price data are dropped into `warnings`; a request where *no* symbol
resolves returns `422`.

## Local dev (optional, needs uv on host)

```bash
uv sync
uv run uvicorn app.main:app --reload
uv run python -m app.smoke      # offline smoke test (no network)
```

## Regenerate the lockfile without installing on the host

```bash
docker run --rm -v "$PWD":/app -w /app \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim uv lock
```

## Wiring

The TS app calls this via `src/server/portfolio.ts` at `PORTFOLIO_SIDECAR_URL`
(default `http://localhost:8000`). Set that env var if you run the sidecar elsewhere.
