---
name: verify
description: Build/launch/drive recipe for verifying changes to this app end-to-end (FastAPI web + Typer CLI).
---

# Verifying this app

Two surfaces: the FastAPI/HTMX web app and the `stocks` Typer CLI. Both share
`app/` code, the SQLite DB, and the `.cache/` KV.

## Isolation

Point everything at a scratch DB so `stocks.db` stays untouched:

```bash
export STOCKS_DB_PATH=/path/to/scratch/verify.db
```

An OpenRouter key is required for research/discover flows. This worktree has no
`.env`; source the main checkout's:

```bash
set -a && source /Users/ian/dev/stocks/.env && set +a
```

## Launch

```bash
uv run uvicorn app.web.app:app --port 8777   # pick a free port
curl -s http://localhost:8777/               # 200 when up (~2s)
```

HTMX pages are shell + partial: drive the partial endpoints directly
(`/research/{sym}/report`, `/ledger/table`, `/portfolio/...`) and grep the HTML.

## Cheap real-LLM run

`uv run stocks research AAPL --cheap` costs ~$0.006 and exercises the full
pipeline (data fetch → LLM → critic → cache → ledger recording). Rerunning the
same day is a cache hit: zero LLM calls, must not re-record a ledger call.

## Ledger-specific

- Fresh calls can't score (not matured). Backdate via sqlite to exercise real
  yfinance scoring on the next `/ledger/table` load or `stocks ledger` run:
  `INSERT INTO calls (symbol, as_of, mode, stance, confidence, price) VALUES ('AAPL','2026-03-02','thorough','bullish','high',240.0)`
- Idempotency probes: reload `/ledger/table` (outcome count unchanged, ~1ms —
  no network when nothing pending); `stocks ledger-backfill` twice (second run
  reports 0).
