# Stock Research Assistant — Plan

An autonomous **research assistant** (not a chatbot) that gathers market data and produces
structured, grounded research reports for human review. We make the decisions; the agent does
the research.

## Core principles

1. **The LLM never emits numbers.** Code fetches every price, ratio, and financial figure. The
   model only *reasons over* and *narrates* data we hand it. Report output is validated against
   the source data. This is the #1 correctness rule.
2. **Structure over chat.** Always produce clean, Zod-validated reports — not freeform prose.
3. **Ground in real methodology.** Fundamental analysis + risk awareness + valuation context.
4. **Cache aggressively + tier models.** Cheap workhorse for most steps, premium only for hard
   synthesis. Keep ongoing cost near zero.
5. **Humans in control.** The agent researches; we decide.
6. **Start narrow.** One ticker → one grounded report, end to end, before anything else.

## Stack (TypeScript, end to end)

| Layer | Choice | Notes |
|---|---|---|
| Language | **TypeScript** (ESM, Node 20+) | No Python in the core loop. |
| Orchestration | **Vercel AI SDK 6** (`ai`) + **Zod** | Linear pipeline; `generateObject` for validated output. No heavy graph framework. |
| Grow-into | **Mastra** (`@mastra/core`) | Migrate *only if* we need parallel steps / retries / durable execution / evals. |
| LLM gateway | **OpenRouter** via `@openrouter/ai-sdk-provider` | Flat 5.5% fee, 300+ models, fallback routing. |
| Workhorse model | **Gemini 2.5 Flash** | Cheap, native JSON, implicit caching. Most steps. |
| Premium model | **Claude Sonnet 4.6** / **Gemini 2.5 Pro** | Hard synthesis only. |
| Market data (free) | **yahoo-finance2** + **Finnhub free** | Prices/fundamentals/news + real-time US quotes (60 req/min). |
| Data (first paid) | **FMP Starter (~$22/mo)** | Only when free tiers pinch. |
| UI (later) | **Next.js** | Better fit than Streamlit for a JS dev. |
| Portfolio opt (deferred) | **Python sidecar** (skfolio / PyPortfolioOpt) | No maintained JS equivalent. Isolate via `child_process`/FastAPI. Don't let 5% dictate the stack. |

### Explicitly avoided
- **Alpha Vantage** as primary — free tier is ~25 req/day, effectively dead.
- **LangChain.js** — `langchain-community` sunset; devs migrating away.
- **LangGraph.js** for now — release lag behind Python, awkward TS idioms; overkill for a linear DAG.
- **IEX Cloud** — shut down Aug 2024.

## Privacy defaults (financial prompts)
- Set `data_collection: "deny"` on every OpenRouter request.
- Never enable prompt logging.
- Prefer Anthropic/Google endpoints; consider excluding DeepSeek (data sovereignty).

## Architecture (initial)

```
src/
  schema.ts          # Zod TickerReport (the validated LLM output shape)
  data/
    yahoo.ts         # yahoo-finance2 wrapper (quote, fundamentals, news)
    finnhub.ts       # Finnhub wrapper (real-time quote, news) — fetch-based
    screener.ts      # yahoo-finance2 predefined screens -> ScreenedQuote[]
    index.ts         # fetchTickerData(symbol) -> TickerData  (the data seam)
  llm/
    provider.ts      # OpenRouter model config (workhorse + premium, lazy)
    research.ts      # researchTicker(symbol, data) -> TickerReport
    discovery.ts     # discoverIdeas(goal) -> validated Candidate[]
    critic.ts        # checkFabrication + critiqueReport + researchTickerReviewed
  index.ts           # CLI: `nr research AAPL` / `nr discover "<goal>"`
```

### Discovery (Phase 2)
The LLM proposes a screen plan: either one of yahoo-finance2's 15 predefined screens
(`undervalued_large_caps`, `growth_technology_stocks`, …) and/or candidate ticker *names* for
a qualitative theme no screen captures. Every candidate is then **validated against real
fetched data** (hallucinated/delisted symbols dropped) and **numeric filters are enforced in
code** against the fetched figures — the model never decides a number passes a threshold.

### Critic (Phase 2)
`researchTickerReviewed` runs research → critique → revise-once (premium model). The critique
combines a **programmatic fabrication check** (every number in the report must trace to a
figure in the data, within tolerance — a hard gate the LLM can't override) with a skeptical
LLM audit (support, balance, missing risks, confidence calibration).

**The data seam** — both modules agree on this contract:

```ts
interface TickerData {
  symbol: string
  fetchedAt: string            // ISO timestamp
  quote: { price: number; currency: string; change: number; changePercent: number } | null
  fundamentals: {              // raw figures, never invented downstream
    marketCap?: number
    peRatio?: number
    forwardPe?: number
    profitMargin?: number
    revenue?: number
    [k: string]: number | undefined
  }
  news: { title: string; url: string; publishedAt: string; source: string }[]
}
```

`researchTicker` receives `TickerData`, passes it to the LLM as ground truth, and returns a
`TickerReport` validated by Zod. The model is instructed to use only the provided figures.

## Roadmap (collapsed from the original 5 phases)

1. **One ticker → one grounded report.** Project + schema + data layer + single `generateObject`
   call on the workhorse model. Runnable via CLI. ✅ *done*
2. **Discovery + critic step.** Screening / idea generation + a reflection pass for quality. ✅ *done*
3. **Web UI (TanStack Start).** Discover ideas / deep-research a ticker / watchlist. ✅ *done*
4. **Cost control + optional portfolio sidecar.** Prompt caching, model tiering, and *only if
   wanted* the Python skfolio service. ✅ *W1–W4 done (instrumentation, premium-chain caching,
   data/report result cache, cheap/thorough tiering); W5 portfolio sidecar deferred.* Detail +
   measured results in [PHASE4.md](PHASE4.md).

### Web UI (Phase 3) — TanStack Start
Chosen over Next.js: internal tool, no SEO, dominated by async server state + server-side
data/LLM calls — which is exactly TanStack Query + server functions. React 19, Vite 8,
Tailwind 4, Nitro. The web layer reuses the existing `src/` core unchanged via **server
functions** (server-only; heavy LLM/data imports never reach the client bundle):

```
src/server/discovery.ts   discover  = createServerFn(...).handler(discoverIdeas)
src/server/research.ts    research  = createServerFn(...) → fetchTickerData + researchTickerReviewed
src/routes/index.tsx          Discover (goal → validated candidates)
src/routes/research.$symbol.tsx   Research (report + critic review)
src/routes/watchlist.tsx          Watchlist (localStorage, no backend)
src/lib/watchlist.ts          useWatchlist() hook
```

The CLI (`nr research` / `nr discover`) and the core modules are untouched and still work.

## Setup

```bash
ni                              # install deps
cp .env.example .env            # add OPENROUTER_API_KEY (+ optional FINNHUB_API_KEY)
nr dev                          # web UI at http://localhost:3000 (Discover / Research / Watchlist)
nr research AAPL                # CLI: deep-dive one ticker (research + critic review)
nr research AAPL --cheap        # workhorse critic, skip revision (cheaper)
nr research AAPL --fresh        # bypass the data + report caches
nr discover "AI infrastructure under $100B"   # CLI: generate validated candidate ideas
nr usage                        # cost/token rollup across recent runs
```
