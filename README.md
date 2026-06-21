# Stock Research

A personal LLM-driven equity-research assistant. **Fully Python**: FastAPI +
HTMX frontend, [Pydantic AI](https://ai.pydantic.dev) over OpenRouter for the
LLM work, `yfinance`/Finnhub for market data, and `skfolio` for portfolio
optimization (all in one process — no sidecar).

## What it does

- **Discover** — describe an investment goal; an LLM proposes a screen / theme,
  candidates are validated against real market data (hallucinated tickers
  dropped, numeric filters enforced in code), then annotated with rationales.
- **Research** — deep-dive a ticker through a *research → critic → revise* chain.
  A programmatic fabrication check plus a skeptical LLM critic guard against
  invented numbers; a cached ground-truth prefix keeps the critic chain cheap.
- **Portfolio** — mean-variance optimization (max-Sharpe / min-risk) over
  historical returns, with an efficient frontier and current-vs-optimal compare.
- **Watchlist** — server-side (SQLite); positions prefill the portfolio page.

## Setup

```bash
cp .env.example .env   # add OPENROUTER_API_KEY (required), FINNHUB_API_KEY (optional)
uv sync
```

## Run the web app

```bash
uv run uvicorn app.web.app:app --reload --port 8000
# open http://localhost:8000
```

## CLI

```bash
uv run stocks research AAPL          # deep-dive (thorough critic chain)
uv run stocks research AAPL --cheap  # workhorse critic, no revision
uv run stocks research AAPL --fresh  # bypass data + report caches
uv run stocks discover "AI infrastructure under $100B market cap"
uv run stocks usage                  # rolling cost / token summary
```

## Test

```bash
uv run pytest
```

## Docker

```bash
docker build -t stocks . && docker run -p 8000:8000 --env-file .env stocks
```

## Layout

```
app/
  data/        market data (yfinance quotes/fundamentals/news, Finnhub, screener)
  llm/         Pydantic AI pipelines: research, critic, discovery, usage tracking
  portfolio/   skfolio mean-variance optimizer (folded-in former sidecar)
  web/         FastAPI app, Jinja2 templates, HTMX partials, static assets
  cache.py     file-based read-through KV (data + report caches)
  db.py        SQLite watchlist store
  schemas.py   Pydantic models / LLM structured-output contracts
  cli.py       Typer CLI
```
