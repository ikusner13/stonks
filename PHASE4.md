# Phase 4 — Cost Control + (Optional) Portfolio Sidecar

Goal: make the assistant cheap enough to run freely and for a small group, **without
sacrificing the grounded/critic quality** built in Phases 1–3. Then, optionally, add the one
genuinely Python-shaped feature (portfolio optimization) as an isolated sidecar.

Guiding rule for this phase: **measure first, then optimize.** Don't tune model tiers or caching
blind — instrument the real token/cost numbers, find the actual hot spots, then cut them.

## Where the money goes today (baseline to confirm with W1)

Per the Phase 3 test runs, one `nr research <SYMBOL>` is up to ~4 LLM calls:

1. research — **workhorse** (Gemini 2.5 Flash), prompt embeds full ground-truth JSON
2. critique — **premium** (Sonnet 4.6), same ground-truth JSON + the report
3. revise — **premium**, same ground-truth JSON + report + issues *(only if medium/high issue)*
4. re-critique — **premium**, ground-truth JSON + revised report

`nr discover "<goal>"` ≈ 2 workhorse calls + N× `fetchTickerData` (no LLM, but N network calls).

So the cost is dominated by **the premium critic chain (calls 2–4) re-sending the same
ground-truth block to the same model.** That is the prime target.

Non-LLM cost: repeated `fetchTickerData` (yahoo/finnhub) on every run, and discovery
re-fetching candidates that research will fetch again.

---

## Workstreams (ordered by leverage)

### W1 — Usage & cost instrumentation  *(do first)*
You can't optimize what you don't measure.

- Capture per-call usage from the AI SDK result (`usage.inputTokens` / `outputTokens`) and the
  OpenRouter cost field, tagged with: model, call-site (`research|critique|revise|discover`),
  symbol/goal, cache-hit tokens, duration.
- Emit one **canonical wide event per run** (one structured log line with the whole picture:
  total calls, tokens in/out, cached tokens, $ cost, revised?). See the `logging-best-practices`
  skill — wide events make "what did this run cost and why" a one-line query.
- Add a tiny rollup: `nr usage` (or a `--cost` flag) that sums recent runs from the log.
- Files: new `src/llm/usage.ts` (helper to record + format), wrap the `generateObject` calls in
  `research.ts` / `critic.ts` / `discovery.ts` so every call flows through it.
- **Done when:** every CLI and web run prints/logs tokens + $ per call and a per-run total.

### W2 — Prompt caching on the premium critic chain  *(biggest single win)*
Calls 2–4 re-send an identical large prefix (the ground-truth JSON + the stable critic system
prompt) to the same premium model. Cache that prefix.

- Restructure the critique/revise prompts so the **stable, repeated content is a cacheable
  prefix** (system instructions + ground-truth data block) and only the small variable tail
  (the specific report / issues) changes per call.
- Add cache breakpoints via the OpenRouter provider. **Verify the current mechanism** in
  `@openrouter/ai-sdk-provider` (Anthropic-style `cache_control: {type:"ephemeral"}` on the
  prefix content part; Gemini/OpenAI cache implicitly). Anthropic cache reads are ~0.1× input
  price; the data block is sent 3× to premium, so this is real money.
- Keep `data_collection: "deny"` and provider **sticky routing** so cache hits land on the same
  provider endpoint across the chain.
- Files: `src/llm/provider.ts` (caching/provider options), `src/llm/critic.ts` (prompt layout).
- **Done when:** W1 shows calls 3–4 reporting cached-input tokens and a materially lower input
  cost than call 2.

### W3 — Result caching (data + reports)
Avoid recomputing what we already have.

- **Data cache:** wrap `fetchTickerData` with a TTL cache (e.g. 15 min intraday) so research +
  discovery don't double-fetch and repeated runs are free. Dependency-light: a `.cache/` JSON
  store or SQLite — no Redis for a personal tool.
- **Report cache:** persist `{ ticker, report, critique }` keyed by `symbol + tradingDay` so
  re-opening a report (CLI re-run or web revisit) costs **$0**. Web already has TanStack Query
  `staleTime` in-memory; this adds a persistent layer shared by CLI + web server fns.
- Add a `--fresh` / "Refresh" affordance to bypass the cache deliberately.
- Files: new `src/lib/cache.ts` (tiny KV with TTL), used by `src/data/index.ts` and a new
  `src/llm/cached-research.ts` (or fold into `critic.ts`).
- **Done when:** a repeated `nr research AAPL` within TTL makes **zero** LLM/network calls
  (confirmed via W1), and the web Research route reuses it.

### W4 — Model tiering & routing controls
Make the quality/cost trade explicit and tunable.

- Config-driven modes: `--cheap` (critic on workhorse, skip revision) vs `--thorough` (current
  premium chain). Default stays thorough (user preference).
- OpenRouter routing knobs: `sort: "price"` / `:floor` for non-critical calls, `allow_fallbacks`
  for resilience. Keep premium critic on the quality endpoint.
- Smarter escalation (optional): only invoke the **premium** critic when the workhorse report is
  low-confidence or the programmatic fabrication check fires; otherwise a cheaper critic pass.
- Files: `src/llm/provider.ts`, small flags in `src/index.ts` + a web toggle.
- **Done when:** the same ticker can be run in cheap vs thorough mode and W1 shows the cost gap.

### W5 — (Optional) Portfolio sidecar  *(only if wanted)*
The one Python-shaped feature. Keep it isolated; do not pull Python into the core.

- Thin Python service (skfolio / PyPortfolioOpt) exposing `optimize(holdings) -> weights/metrics`,
  called from a TS server function via `child_process` (JSON in/out) or a tiny FastAPI endpoint.
- Inputs come from the watchlist / manually entered positions; outputs are mean-variance weights,
  risk metrics, an efficient-frontier point — presented as **research context, not advice**.
- Files: `sidecar/optimize.py`, new `src/server/portfolio.ts`, a `/portfolio` route.
- **Done when:** a watchlist of held names returns a grounded allocation view; the core TS app
  still runs with the sidecar absent (feature degrades, doesn't crash).

---

## Suggested sequencing
W1 → W2 → W3 are the cost core and should land in that order (measure, cut the biggest item, then
stop recomputing). W4 is polish on top. W5 is independent and optional — pick it up only when
portfolio construction is actually on the table.

## Non-goals for Phase 4
- No new data vendors (still yahoo-finance2 + optional Finnhub; FMP is a later "when serious"
  step, not a cost-control task).
- No auth / multi-tenant / database — file/SQLite cache is enough for a small group.
- No rewrite of the research/critic logic — Phase 4 wraps and tunes it, it doesn't replace it.

## Acceptance criteria (phase-level)
- [x] Every run reports tokens + $ cost (W1). One wide event per run → `.cache/usage.jsonl`; `nr usage` rolls it up; CLI + web print it to stderr.
- [x] Premium critic chain shows cached-prefix savings (W2). Measured on AAPL: `re-critique` read 33,332 / 35,097 input tokens from cache, cutting that call from $0.143 → $0.029 (~80% lower input cost).
- [x] Repeated same-day research/discovery hits cache → ~$0 (W3). Warm re-run of `nr research AAPL` = 0 LLM/network calls, $0.
- [x] Cheap vs thorough mode selectable, with a measured cost delta (W4). AAPL thorough $0.339 vs MSFT cheap $0.031 (~11×). `--cheap`/`--thorough`/`--fresh` flags + web mode/refresh toggle.
- [ ] (If pursued) portfolio sidecar returns an allocation view and is fully optional (W5). **Deferred — not built; optional, pick up only when portfolio construction is on the table.**

## What landed (implementation notes)
- `src/llm/usage.ts` — `withRun()` (AsyncLocalStorage run context) + `tracked()` per-call capture from AI SDK `usage` and OpenRouter usage-accounting (`providerMetadata.openrouter.usage` → `cost`, `promptTokensDetails.cachedTokens`); wide-event log + `formatRollup()`.
- `src/llm/provider.ts` — usage accounting on (`usage:{include:true}`), `data_collection:"deny"`, sticky premium routing (`allow_fallbacks:false`) for cache-hit stickiness; `workhorse()`/`premium()` take per-call settings.
- `src/llm/critic.ts` — one shared system + a single ephemeral-cache-marked ground-truth block reused across critique/revise/re-critique, so the two same-schema critique calls share the cached prefix. **Finding:** the `revise` call (call 3) does *not* hit — it emits a different output schema, and Anthropic's cache prefix (tools→system→messages) diverges at the tool/schema. The dominant repeated re-send (call 4) does hit. Also adds `--cheap`/thorough mode tiering.
- `src/lib/cache.ts` — tiny TTL KV under `.cache/`. `src/data/index.ts` data-caches `fetchTickerData` (15 min). `src/llm/cached-research.ts` report-caches `{ticker, report, critique}` by `symbol+tradingDay+mode`.
- CLI: `nr research <SYM> [--cheap] [--fresh]`, `nr usage`. Web: `research` server fn takes `{symbol, mode, fresh}`; research route has mode + Refresh buttons.
