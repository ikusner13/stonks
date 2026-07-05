# Model eval — July 5, 2026

61 successful runs, $5.45 total spend. Harness: `scripts/eval_models.py` (report pipeline,
subprocess-isolated per model config, shared data cache) + `scripts/eval_discovery.py`
(discovery flow). Raw runs in `eval_results/20260705-*/` and `eval_results/discovery/`.
Prompts held fixed (the app's current prompts); failures classified capability vs
discipline vs prompt-fixable.

## Recommendation

| Tier | Current default | Recommended | Why |
|---|---|---|---|
| `WORKHORSE_MODEL` | `google/gemini-3.5-flash` ($1.50/$9.00) | **`google/gemini-3.1-flash-lite`** ($0.25/$1.50) | 8× cheaper ($0.004 vs $0.034/report), 2× faster (7s vs 15s), *better* discipline: fewer suspicious figures (9 vs 19), no raw-float dumps, no transcription errors, better-calibrated confidence, and in discovery it honestly flags mismatched candidates instead of justifying them. |
| `PREMIUM_MODEL` | `anthropic/claude-sonnet-4.6` ($3/$15) | **`anthropic/claude-sonnet-5`** ($2/$10 intro → $3/$15 after Aug 31) | Sonnet-4.6 via OpenRouter's shared pool 429'd on 10/10 attempts across two rounds (`allow_fallbacks: False` makes that a hard failure) — it is effectively down as configured. Sonnet-5 had the best critic judgment: correctly diagnosed fab-checker false positives as period-label noise while catching real issues (headline-sourced analyst target cited as data; P/E-vs-broken-financials conflation), half the cost of opus-4.8/gpt-5.5, most conservative confidence suggestions. |

Max-quality alternative for premium: `anthropic/claude-opus-4.8` ($0.10/report post-fix) —
the only critic that said "manual inspection shows every flagged number is a false
positive" and its findings were the subtlest (e.g. FCF defined as OCF−capex when capex
isn't in the data). Worth it if thorough-mode reports drive real money decisions.

## Per-report cost (measured, mean)

| Config | Today | After fab-checker fix |
|---|---|---|
| cheap / flash-lite | $0.004 | $0.004 |
| cheap / glm-5.2 | $0.015 | $0.015 |
| cheap / gemini-3.5-flash (incumbent) | $0.034 | $0.034 |
| thorough / sonnet-5 | $0.153 | **$0.062** |
| thorough / gemini-3.1-pro | $0.220 | $0.086 |
| thorough / opus-4.8 | $0.294 | $0.104 |
| thorough / gpt-5.5 | $0.307 | $0.129 |

Discovery: $0.0002–0.005 per goal — negligible.

Projection at ~10 thorough + 20 cheap reports/day (recommended pair, post-fix):
≈ $0.70/day ≈ **$21/month**. Current defaults (if sonnet-4.6 worked): ≈ $47/month.

## Disqualified

- **kimi-k2.6** (workhorse): timed out (>420s) or failed schema on 5/5 runs.
- **minimax-m3** (workhorse): upstream 429s + schema failures, 5/5.
- **claude-haiku-4.5** (workhorse): best prose and sharpest self-audit of the cheap tier,
  but 2/5 hard schema failures (no native json_schema via OpenRouter) and habitual
  derived arithmetic ($228B = 4×57B annualization; computed P/S, net-cash). The
  derivation habit is prompt-fixable; the schema failures aren't.
- **deepseek-v4-flash** (workhorse): cheapest ($0.002) but 91s/run and worst grounding
  discipline (29 suspicious figures incl. wrong values in verbatim key_metrics).
- **gpt-5-mini** (workhorse): unreadable output (raw floats: "market capitalization of
  9702044672.0"), 82s/run, self-audit parrots the buggy fab hint as high-severity.
- **glm-5.2** (workhorse): good reports and smart self-audit, but dangerous in discovery —
  invented a goal when given none, confidently asserted wrong sectors (called mortgage
  REITs "healthcare"), leaked numbers into rationales 7×. Also 113s/run.
- **deepseek-v4-pro** (premium): 400 on every call — its provider rejects forced
  tool_choice in thinking mode, incompatible with pydantic-ai structured output.
- **gemini-3.1-pro** (premium): invents stricter rules than the checker (flags 147.85→"148"
  rounding as high-severity fabrication), most verbose critic (12–19k output tokens).
- **gpt-5.5** (premium): capable but very literal — obeys "treat the hint as authoritative"
  even while acknowledging the flags are naming artifacts; revised reports degrade into
  raw-value dumps. Prompt-fixable (see below) but then still 2× sonnet-5's cost.

## App bugs the eval surfaced

1. **`check_fabrication` false positives** (`app/llm/critic.py`): `_prose_numbers` exempts
   0–10 and years but not finance phrase-numbers — 52 (52-week), 200/50 (200d/50d SMA),
   12/14/90 (periods), 500 (S&P 500), 1e6/1e9 magnitude words. Every report in 61 runs
   "failed", forcing revise+re-critique on 100% of thorough runs (~2.5× premium cost).
   Fix: allowlist those tokens (or strip indicator labels/keys before parsing prose).
2. **SEC extraction wrong for banks** (`app/data/sec.py`): JPM ground truth had quarterly
   net income ($57B) > quarterly revenue ($27B) and total assets $270B vs liabilities
   $4.06T. XBRL field mapping breaks on bank filings; models rightly flagged the anomaly.
3. **`allow_fallbacks: False` has no recovery path**: correct for Anthropic cache
   stickiness, but when the pinned provider is saturated every thorough run hard-fails.
   Consider retry-with-fallbacks-on-429, or BYOK Anthropic key on OpenRouter.
4. **Discovery never validates sector/theme fit**: `_passes` checks only mcap/P/E; the
   rationale prompt invites post-hoc justification of wrong-sector candidates. Add a
   code-side sector check (Yahoo sector field) or a validate-fit yes/no LLM step that is
   allowed to reject candidates.

## Prompt notes (per user: prompts are per-model)

- `AUDIT_GUIDANCE` says "treat the fabrication hint as authoritative" — literal models
  (gpt-5.5, gemini-3.1-pro) escalate known-FP flags to high severity; Claude models
  exercise discretion. After fixing bug #1, also reword to: "the hint is a heuristic;
  verify each flag against the data before treating it as fabrication."
- Claude-tier models tolerate the current prompts best; that's partly why they win under
  a frozen-prompt eval. gpt-5.5 with a verify-then-escalate instruction would rerank
  above gemini-3.1-pro, though still at 2× sonnet-5's cost.
- If haiku-4.5 is ever revisited: add "never perform arithmetic; only restate figures"
  and it may become viable — its judgment was genuinely good.

## Caveats

- One trading day, 5 tickers (AAPL, NVDA, JPM, CAVA, OSCR), n=5 per config. Rankings are
  directional, not statistical.
- Discovery eval: a zsh 1-based-array bug fed goal "" to the first round (which became a
  useful empty-goal robustness test) and skipped the dividend goal entirely.
- Sonnet-5 pricing is introductory until 2026-08-31; post-intro thorough cost ≈ $0.09.
- OpenRouter provider routing varies; the sonnet-4.6 saturation and deepseek/minimax
  failures reflect this week's provider pool, not the models' inherent quality.
