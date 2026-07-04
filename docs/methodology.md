# Methodology

How this app decides anything about a stock or a portfolio. Every number here
is either fetched from a named source or computed by code in this repo — this
document states formulas, thresholds, and TTLs exactly as coded, not as
originally designed. Where the two disagree, the code is authoritative.

## 1. Data layer & trust model

Each ticker's `TickerData` is assembled from independent sources, fetched
concurrently (`app/data/__init__.py::_fetch_uncached`), each wrapped so a
failure in one never blocks the others:

| Source | Provides | Cache namespace | TTL | Notes |
| --- | --- | --- | --- | --- |
| Yahoo Finance (`yfinance`) | Quote (fallback), fundamentals (market cap, trailing/forward P/E, profit margin, revenue), news, price history | `data` (merged blob) | 15 min | Always attempted; the only source with no API key requirement. |
| Finnhub (optional) | Quote (preferred when present), company news | `data` (merged blob) | 15 min | Skipped entirely — not even attempted — when `FINNHUB_API_KEY` is unset. |
| SEC/EDGAR (`edgartools`) | Revenue, net income, gross/operating income, total assets/liabilities, cash, total debt, shares outstanding, operating cash flow, free cash flow, fiscal period/form/filed date | `sec` | 24 h | Identity string required by EDGAR comes from `SEC_IDENTITY`, falling back to a hardcoded address. |
| FRED (optional) | Fed funds rate, CPI YoY, 10y treasury, unemployment rate, GDP growth | `macro` | 6 h | Single global cache key (`"latest"`) — not per-symbol. Entirely skipped when `FRED_API_KEY` is unset. |

**Source-status model.** Every source populates one entry in `TickerData.sources`
with one of four values:
- `ok` — fetched and non-empty.
- `empty` — fetched successfully but returned nothing usable (e.g. no news
  items, all-null fundamentals).
- `error` — the fetch raised; a safe fallback (`None`/`[]`/empty model) is
  substituted so the rest of the pipeline still runs.
- `disabled` — the source was never attempted because its API key is unset
  (currently only `macro`).

The quote and news fields are each backed by *two* sources (Finnhub + Yahoo);
their status is derived after merging: `quote` is `ok` if either producer
returned one, `error` only if both attempts raised, else `empty`. `news` is
deduplicated by URL (Finnhub entries first) and capped at 15 items; its status
follows the same ok/error/empty logic over the merged list.

**Failures are never cached as failures.** `with_cache` (`app/cache.py`) only
persists a result when `produce()` returns a non-`None` value; anything that
raises propagates without writing to disk. Two different failure shapes exist
in this codebase, and they behave differently:
- A **fully failed fetch** (e.g. `research_ticker_cached` raising
  `InsufficientDataError` when there's neither a quote nor a market cap) never
  reaches `write_cache` — the next call retries from scratch.
- A **partial per-source failure** inside an otherwise successful fetch (e.g.
  Finnhub down but Yahoo fine) is captured as `sources[name] = "error"` *within*
  a `TickerData` blob that itself still gets cached for the source's TTL —
  because the overall `produce()` succeeded. A partial failure is retried only
  when that TTL expires or the caller passes `fresh=True`.

This status surfaces directly in the UI as chips (ok/empty/error/disabled) next
to each source, and feeds the confidence caps described in §4.

## 2. Indicator engine

`app/indicators/engine.py::compute_indicators` computes 12 indicators from
price history (`yfinance`, 420 calendar days back) and the fetched
`TickerData`. Every value is deterministic — the LLM never computes or alters
one, only reads and narrates it. An indicator whose inputs are insufficient
gets `signal: "unavailable"` and a `value: null` rather than a guess.

| Key | Formula (as coded) | Min data | Bullish | Bearish | Evidence rationale |
| --- | --- | --- | --- | --- | --- |
| `momentum_12_1` | `close[-21] / close[-252] - 1` | 252 closes | `> +10%` | `< -10%` | 12-month-minus-1-month momentum; the 1-month gap is the standard exclusion for short-term reversal. |
| `momentum_6m` | `close[-1] / close[-126] - 1` | 126 closes | `> +8%` | `< -8%` | Shorter-horizon momentum effect. |
| `pct_from_52w_high` | `close[-1] / max(close[-252:]) - 1` | 60 closes | `> -5%` | `< -20%` | Proximity to a 52-week high (relative-strength effect); a deep discount from the high is a risk flag. |
| `trend_200d` | `close[-1] / mean(close[-200:]) - 1` | 200 closes | `> 0%` | `< -2%` | Price above/below its long moving average — classic trend-following signal. |
| `realized_vol_90d` | `std(daily returns, last 90) * sqrt(252)` | 90 closes | — (never bullish) | `> 60%` annualized | Realized volatility as a risk flag, not an opportunity signal. |
| `beta_1y` | `cov(symbol returns, SPY returns) / var(SPY returns)`, aligned daily returns, last 252 rows | 200 aligned return rows, nonzero SPY variance | — | — | Always `neutral`; informational market-risk context only, no bullish/bearish read. |
| `max_drawdown_1y` | `min(window / cummax(window) - 1)` over last 252 (or fewer) closes | 60 closes | — (never bullish) | `< -40%` | Drawdown depth as a risk flag. |
| `earnings_yield` | `1 / trailing P/E` (P/E from Yahoo fundamentals) | P/E present and `> 0` | `> 6%` | `< 2%` | Value factor (inverse P/E). If P/E is unusable *and* SEC net income is negative, the indicator is forced `bearish` with `value: null` ("negative trailing earnings") instead of `unavailable`. |
| `fcf_yield` | `free_cash_flow / market_cap` (FCF from SEC, cap from Yahoo) | both present, cap `> 0` | `> 5%` | `< 1%` | Quality/value factor — cash generation relative to price. |
| `profit_margin` | Yahoo `profitMargins`, used directly | present | `> 15%` | `< 0%` | Quality factor (profitability). |
| `debt_to_assets` | `total_debt / total_assets` (SEC) | both present, assets `> 0` | `< 15%` (reversed polarity — low leverage is bullish) | `> 50%` | Leverage/quality risk flag. |
| `days_to_earnings` | Calendar date diff from Yahoo's earnings calendar | earnings date known | — | — | Always `neutral`; timing context (event-risk window around earnings), not a directional signal. |

`beta_1y` and `days_to_earnings` are informational-only: they carry no entry in
the internal threshold table and always report `neutral`. The other 10 map to
a `(bullish_at, bearish_at)` pair; values strictly beyond the threshold flip
the signal, otherwise it's `neutral`.

The scorecard (`IndicatorScorecard`) tallies `bullish`/`bearish`/`neutral`/
`unavailable` counts and a `data_completeness` fraction (`indicators with a
non-null value / 12`). It is cached under namespace `scorecard`, keyed by
`SYMBOL:YYYY-MM-DD` (UTC date), TTL 24 h.

## 3. LLM research pipeline

**Grounding contract.** Every prompt in the pipeline states the same absolute
rules, verbatim from `app/llm/research.py` and `app/llm/critic.py`:
- Reason only over numeric figures explicitly present in the provided JSON
  ground truth; never invent, estimate, extrapolate, or recall a number from
  memory.
- In `key_metrics`, `value` must be restated verbatim from the JSON, not
  computed fresh.
- If a figure is missing or null, say so explicitly rather than guessing.
- The indicator scorecard is treated as ground truth too; `indicator_view`
  must address every indicator whose signal isn't `unavailable`, naming
  disagreements between indicators rather than smoothing them over.
- Default to skepticism — sparse or null-heavy data should lower confidence.

**What's injected**: the full `TickerData.model_dump()` (quote, fundamentals,
news, SEC financials, macro, source statuses) plus the full
`IndicatorScorecard.model_dump()`, both as raw JSON in the prompt.

**The chain** (`app/llm/critic.py::research_ticker_reviewed`):
1. **Research** — workhorse model drafts a `TickerReport` from the ground
   truth (always the same model/step regardless of mode).
2. **Audit** — a critic agent reviews the draft against the ground truth. In
   **thorough** mode this call is routed to the premium model; in **cheap**
   mode it stays on the workhorse model — i.e. cheap mode's critic is the same
   model that wrote the report, a same-model self-audit rather than an
   independent check.
3. **Revise** (thorough mode only, conditional) — triggered only if the audit's
   fabrication check failed or any issue has `medium`/`high` severity. A
   premium-model agent rewrites the report to fix every listed issue, dropping
   any unsupported figure rather than replacing it with another guess.
4. **Re-critique** (thorough mode only, follows a revision) — the same premium
   audit runs again against the revised report, producing the final critique.

   Cheap mode always stops after step 2, regardless of what the audit finds.

The ground-truth JSON prefix is built once per report and reused, unchanged,
across every call in the chain, with an OpenRouter/Anthropic `CachePoint(ttl="1h")`
inserted after it — so steps 2–4 hit the cached-token price for that shared
prefix instead of paying full input price each time. This is the main lever
behind thorough mode's cost (§ Cost profile in the README) staying in the
$0.10–0.40 range rather than scaling linearly with the number of calls.

**Programmatic fabrication check** (`check_fabrication` in `app/llm/critic.py`).
Runs before every audit/re-critique call, independent of the LLM.
- **Scope** — scans these report fields for numeric tokens: `key_metrics[*].value`
  (strict numeric parse) and `key_metrics[*].interpretation`, `valuation_context`,
  `indicator_view`, `summary`, `thesis.bull[*]`, `thesis.bear[*]`, `risks[*]`,
  `things_to_investigate[*]` (prose parse, which additionally ignores bare
  integers 0–10 and 4-digit years 1900–2100 to skip counts and dates).
- **Allowed set** — every number found in `fundamentals`, `quote`, SEC
  financials, macro context, indicator scorecard values, and news *headlines*
  (not article bodies, and never timestamps/URLs).
- **The 2% rule** — a candidate number is "grounded" if its absolute magnitude
  is within 2% relative tolerance of *any* allowed number (sign is ignored;
  the check assumes direction is conveyed by surrounding words). A `%` token
  is checked against both its literal value and its `/100` form, absorbing
  fraction-vs-percent restatement.
- **Hard override** — if the programmatic check fails, the code forcibly sets
  `critique.fabrication_check.passed = False` regardless of what the LLM
  critic concluded, appending the LLM's own explanation to the programmatic
  one. The LLM can never soften a hard-caught fabrication back to "passed";
  it can only add detail or catch additional issues the regex missed.
- **Honest limitation** — the check is **semantically blind**: it only tests
  whether a number's magnitude coincides with *some* real figure anywhere in
  the ground truth, not whether it's attached to the right claim. A fabricated
  "revenue growth of 12%" would pass if any unrelated real figure (say, a P/E
  of 12.1) happens to fall within 2% of it. It catches invented magnitudes; it
  cannot catch a real number wired to the wrong fact.

## 4. Confidence

`app/indicators/confidence.py::compute_confidence` builds a completeness score
by summing fixed weights (max 1.0):

| Signal | Weight |
| --- | --- |
| Quote present | 0.25 |
| ≥2 of 5 fundamentals fields present (market cap, trailing/forward P/E, margin, revenue) | 0.15 |
| SEC financials present | 0.20 |
| ≥3 news items | 0.10 |
| Macro context present | 0.05 |
| Indicator scorecard `data_completeness` | × 0.25 |

Completeness maps to a base grade: `≥0.75` → `high`, `≥0.45` → `medium`,
else `low`.

**Hard caps**, applied after the base grade, each only able to lower it:
- Any source with status `error` → capped at `medium`.
- No quote at all → capped at `low` (this cap can stack after the error cap,
  landing at `low` even if completeness alone would say `medium`).

**Final clamp.** The report's own stated `confidence`, the critic's
`suggested_confidence`, and this computed grade are combined with
`clamp_confidence(*grades) = min(grades, key=order)` (`low < medium < high`)
in `research_ticker_cached`. The model can only ever pull confidence down from
what the data supports — never up.

**Position sizing** (`app/portfolio/decision_support.py::suggest_position_size`)
maps the final confidence to a starting-size band, as a fraction of total
portfolio value:

| Confidence | Band |
| --- | --- |
| high | 5.0–10.0% |
| medium | 3.0–6.0% |
| low | 1.5–3.0% |

If the symbol is already held, the existing weight is treated as consumed
headroom within the band: the suggested dollar range narrows to
`band − current_weight` (floored at 0), and if the current weight already
meets or exceeds the band's high end, the guidance switches to "already at
band — adding would increase concentration."

## 5. Portfolio math

**Valuation aggregation** (`app/portfolio/holdings.py::value_holdings`).
`total_value`, `total_cost`, and `total_unrealized_pl` are summed **only**
over holdings that priced successfully this call; a holding with no fetchable
quote contributes nothing to any total and its symbol is listed in
`unpriced_symbols` (it's still shown in the table, with `market_value: null`).
Cost/P&L additionally require `avg_cost` to have been recorded — omit it and
that holding is excluded from cost/P&L totals even when priced. Per-holding
`weight` is `market_value / total_value` and is only computed once every
holding's price is known.

**Allocation backtest** (`app/portfolio/performance.py::compute_performance`).

> **This is not the account's realized return.** It replays *today's* live
> weights, held constant, over the lookback window (730 days / ~2 years by
> default) against a benchmark (SPY by default) using `quantstats_lumi`. It
> answers "what would CAGR/Sharpe/Sortino/volatility/max-drawdown have been if
> I'd held this exact allocation the whole time" — not what this account
> actually earned, since real holdings and weights changed over that period
> and no transaction history feeds this calculation.

Requires ≥30 days of overlapping portfolio return history or it returns
`None` (no metrics shown); benchmark CAGR is computed only over the date
intersection with the portfolio series, also gated at ≥30 common rows.
Symbols with too little price history are excluded per the rules below and
listed in `excluded_symbols`.

**Optimizer** (`app/portfolio/optimize.py::optimize`). Mean-variance
optimization via `skfolio.MeanRisk` with `RiskMeasure.VARIANCE`. `max_sharpe`
maps to `MAXIMIZE_RATIO`, `min_risk` to `MINIMIZE_RISK`. `risk_free_rate`
(0–20%, default 0) feeds both skfolio's objective and the independently
computed Sharpe (`(annualized return − rf) / annualized volatility`, using
sample mean × 252 and sample covariance × 252 — no shrinkage).

- **Per-asset cap**: `max_weight` (default 0.35) is relaxed to `1/n` if the
  requested cap is infeasible for `n` assets (e.g. 4 assets can't each stay
  under 20%); a warning records the relaxation.
  A single-asset portfolio (`n=1`) skips optimization entirely (weight = 100%).
- **Efficient frontier**: only computed when `n ≥ 2` and `frontier_points ≥ 2`
  (default 20), via a separate `MINIMIZE_RISK` sweep at `efficient_frontier_size`
  points.
- **Why it's a starting point, not a target**: the result carries a fixed
  disclaimer ("Research context only, not investment advice... assumes the
  past is representative"), and mean-variance optimization on sample means is
  well known to be estimation-error-prone — the per-asset cap bounds any single
  extreme weight but does not fix the underlying estimation-error sensitivity.

**Drift** (`app/portfolio/decision_support.py::analyze_drift`). Per symbol,
`drift = current_weight − optimizer_target_weight`; `significant` if
`abs(drift) > 0.05` (5 percentage points), sorted by largest absolute drift
first. Returns `None` entirely if there's no current allocation to compare
(e.g. holdings carry no value/shares).

**Correlation** (`app/portfolio/decision_support.py::analyze_correlation`,
`compute_correlation_insight`). Return history for all symbols is fetched and
cleaned via `app/portfolio/history.py::drop_short_history`: a symbol is
excluded if its non-null row count is below
`max(60, 0.5 × the best-covered symbol's row count)`, and after that filter
all remaining rows with any remaining gap are dropped (row-wise), so the
matrix is always fully dense. Requires ≥2 symbols surviving with ≥60 overlapping
rows total, else returns `None`. Pairwise Pearson correlation over daily
returns; a pair is "high" at `≥ 0.80`. Portfolio-level `level`:
`high` if `avg_correlation ≥ 0.70` or ≥3 high pairs; `moderate` if
`avg_correlation ≥ 0.40` or any high pair exists; else `low`. Cached per
symbol-set + lookback + trading day (namespace `correlation`, 24 h TTL).

## 6. Known limitations

- **No outcome tracking.** Nothing in this codebase records what a report
  predicted against what actually happened later — there is no calibration
  loop scoring past confidence or theses against subsequent price action.
- **No lot-level cost basis.** `upsert_holding` overwrites `shares`/`avg_cost`
  on conflict; adding to a position at a new price replaces the average
  rather than tracking individual tax lots.
- **No cash tracking.** Portfolio weights are equity-only — there's no
  concept of un-invested cash, so a portfolio with a large uninvested cash
  balance shows equity-only weights that don't reflect true concentration.
- **Fabrication check is magnitude-only**, not semantic (see §3) — it can
  match a fabricated number to an unrelated real figure that happens to be
  numerically close.
- **Single-currency assumption.** Holdings valuation and portfolio math treat
  every quote as USD; a non-USD `Quote.currency` value is stored but never
  converted before being summed into totals or fed into return calculations.
- **Mean-variance on sample means is estimation-error-prone**, a standard
  critique of unconstrained Markowitz optimization; the per-asset cap bounds
  the size of any single mistake but does not address the underlying
  sensitivity to noisy return estimates.
- **Cheap mode's critic is a same-model self-audit** — the same workhorse
  model that wrote the report also audits it, rather than an independent
  premium reviewer, which is a materially weaker check than thorough mode.
