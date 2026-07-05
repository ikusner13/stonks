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
| Yahoo Finance (`yfinance`) | Quote (fallback), fundamentals (market cap, trailing/forward P/E, profit margin, revenue, sector, industry), news, price history | `data` (merged blob) | 15 min | Always attempted; the only source with no API key requirement. |
| Finnhub (optional) | Quote (preferred when present), company news | `data` (merged blob) | 15 min | Skipped entirely — not even attempted — when `FINNHUB_API_KEY` is unset. |
| SEC/EDGAR (`edgartools`) | Revenue, net income, gross/operating income, total assets/liabilities, cash, total debt, shares outstanding, operating cash flow, free cash flow, fiscal period/form/filed date | `sec` | 24 h | Identity string required by EDGAR comes from `SEC_IDENTITY`, falling back to a hardcoded address. Field extraction prefers exact `us-gaap` tags with sanity validation for assets vs. liabilities and net income vs. revenue; wrong-magnitude bank statements motivated this. |
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
returned one; if neither did, it's `error` if *either* attempt raised (not
only if both did), else `empty`. `news` is deduplicated by URL (Finnhub
entries first) and capped at 15 items; its status follows the same
ok/error/empty logic over the merged list.

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
to each source, and feeds the confidence caps described in §5.

## 2. Indicator engine

`app/indicators/engine.py::compute_indicators` computes the active profile's
ordered indicator list from price history (`yfinance`, 420 calendar days back)
and the fetched `TickerData`. Large-cap research computes 12 indicators; penny
research computes 15. Every value is deterministic — the LLM never computes or
alters one, only reads and narrates it. An indicator whose inputs are
insufficient gets `signal: "unavailable"` and a `value: null` rather than a
guess.

| Key | Formula (as coded) | Min data | Evidence rationale |
| --- | --- | --- | --- |
| `momentum_12_1` | `close[-21] / close[-252] - 1` | 252 closes | 12-month-minus-1-month momentum; the 1-month gap is the standard exclusion for short-term reversal. |
| `momentum_6m` | `close[-1] / close[-126] - 1` | 126 closes | Shorter-horizon momentum context. |
| `pct_from_52w_high` | `close[-1] / max(close[-252:]) - 1` | 60 closes | Proximity to a 52-week high; a deep discount from the high is a risk flag. |
| `trend_200d` | `close[-1] / mean(close[-200:]) - 1` | 200 closes | Price above/below its long moving average. |
| `realized_vol_90d` | `std(daily returns, last 90) * sqrt(252)` | 90 closes | Realized volatility as a risk flag. |
| `beta_1y` | `cov(symbol returns, SPY returns) / var(SPY returns)`, aligned daily returns, last 252 rows | 200 aligned return rows, nonzero SPY variance | Informational market-risk context only. |
| `max_drawdown_1y` | `min(window / cummax(window) - 1)` over last 252 (or fewer) closes | 60 closes | Drawdown depth as a risk flag. |
| `earnings_yield` | `1 / trailing P/E` (P/E from Yahoo fundamentals) | P/E present and `> 0` | Value factor (inverse P/E). If P/E is unusable *and* SEC net income is negative, the indicator is forced `bearish` with `value: null` ("negative trailing earnings") instead of `unavailable`. |
| `fcf_yield` | `free_cash_flow / market_cap` (FCF from SEC, cap from Yahoo) | both present, cap `> 0` | Quality/value factor — cash generation relative to price. |
| `profit_margin` | Yahoo `profitMargins`, used directly | present | Quality factor (profitability). |
| `debt_to_assets` | `total_debt / total_assets` (SEC) | both present, assets `> 0` | Leverage/quality risk flag. |
| `days_to_earnings` | Calendar date diff from Yahoo's earnings calendar | earnings date known | Timing context around earnings, not a directional signal. |
| `trend_50d` | `close[-1] / mean(close[-50:]) - 1` | 50 closes | Shorter trend window for securities that often lack 200 trading days of usable history. |
| `avg_dollar_volume_20d` | `mean(close[-20:] * volume[-20:])` | 20 aligned close+volume rows | Tradability and exit/liquidity context. |
| `relative_volume` | `volume[-1] / mean(volume[-21:-1])` | 21 volume rows and nonzero denominator | Confirms whether current volume is unusual. |
| `zero_volume_days_90d` | `int((volume[-90:] == 0).sum())` | 90 volume rows | Illiquidity flag. |
| `share_dilution` | `shares_outstanding / shares_outstanding_prior - 1` | current and prior SEC share counts, prior `> 0` | Period-over-period dilution; the detail names both SEC periods. |
| `cash_runway_months` | if `operating_cash_flow < 0`: `cash_and_equivalents / (abs(operating_cash_flow) / 12)`; if `operating_cash_flow >= 0`: `value: null`, forced `bullish` | cash and operating cash flow present | Survival runway for cash-burning companies; positive operating cash flow is treated as self-funding. |
| `filing_recency_days` | `(today_utc - date(financials.filed)).days` | parseable SEC filed date | Stale-filer risk. |
| `float_shares` | `fundamentals.float_shares` | Yahoo float shares present | Float/squeeze/volatility context; detail shows millions. |

Signal thresholds are profile-owned policy (§3). A missing threshold entry means
the indicator is informational and reports `neutral` when it has a value. A
threshold entry maps to `(bullish_at, bearish_at, reversed_polarity)`; values
strictly beyond the threshold flip the signal, otherwise it is `neutral`.

The scorecard (`IndicatorScorecard`) tallies `bullish`/`bearish`/`neutral`/
`unavailable` counts and a `data_completeness` fraction (`indicators with a
non-null value / len(indicators)`). It is cached under namespace `scorecard`,
keyed by `SYMBOL:PROFILE:YYYY-MM-DD` (UTC date), TTL 24 h.

## 3. Profiles

Profiles are frozen policy objects in `app/profiles/`. They decide which
indicators run, how those indicators are thresholded, how completeness maps to
confidence, how position-size bands are chosen, what stance is injected into
the LLM prompt, and whether a security is eligible for mean-variance
optimization.

**Selection rules** (`app/profiles/__init__.py::select_profile`), first match
wins:

1. Manual override (`--profile penny|largecap` or `?profile=penny|largecap`) →
   that profile, reason `"manual override"`.
2. `fundamentals.exchange in {"PNK", "OTC", "OEM", "OQB", "OQX"}` → penny,
   reason `"OTC-listed ({exchange})"`.
3. `quote.price < 5.0` → penny, reason `"price ${price} < $5"`.
4. `fundamentals.market_cap < 75_000_000` → penny, reason
   `"market cap ${cap} < $75M"`.
5. Otherwise, including all fields missing → largecap, reason `"default"`.

Report caching carries the override, not the derived profile, because profile
selection happens inside the cached `produce()` function after data is fetched:
`f"{sym}:{day}:{mode}:{profile_override or 'auto'}"`. Scorecard caching carries
the derived profile: `f"{sym}:{profile.key}:{today}"`.

**Large-cap profile** (`app/profiles/largecap.py`):

| Field | Value |
| --- | --- |
| `key` | `largecap` |
| `label` | `Large cap` |
| `indicator_keys` | `momentum_12_1`, `momentum_6m`, `pct_from_52w_high`, `trend_200d`, `realized_vol_90d`, `beta_1y`, `max_drawdown_1y`, `earnings_yield`, `fcf_yield`, `profit_margin`, `debt_to_assets`, `days_to_earnings` |
| `optimizer_included` | `True` |
| `research_stance` | `""` |

| Key | `bullish_at` | `bearish_at` | `reversed_polarity` |
| --- | ---: | ---: | --- |
| `momentum_12_1` | `0.10` | `-0.10` | `False` |
| `momentum_6m` | `0.08` | `-0.08` | `False` |
| `pct_from_52w_high` | `-0.05` | `-0.20` | `False` |
| `trend_200d` | `0.0` | `-0.02` | `False` |
| `realized_vol_90d` | `nan` | `0.60` | `False` |
| `beta_1y` | no entry | no entry | no entry |
| `max_drawdown_1y` | `nan` | `-0.40` | `False` |
| `earnings_yield` | `0.06` | `0.02` | `False` |
| `fcf_yield` | `0.05` | `0.01` | `False` |
| `profit_margin` | `0.15` | `0.0` | `False` |
| `debt_to_assets` | `0.15` | `0.50` | `True` |
| `days_to_earnings` | no entry | no entry | no entry |

**Penny / micro-cap profile** (`app/profiles/penny.py`):

| Field | Value |
| --- | --- |
| `key` | `penny` |
| `label` | `Penny / micro-cap` |
| `indicator_keys` | `trend_50d`, `trend_200d`, `pct_from_52w_high`, `momentum_6m`, `realized_vol_90d`, `max_drawdown_1y`, `avg_dollar_volume_20d`, `relative_volume`, `zero_volume_days_90d`, `share_dilution`, `cash_runway_months`, `filing_recency_days`, `debt_to_assets`, `float_shares`, `days_to_earnings` |
| `optimizer_included` | `False` |

| Key | `bullish_at` | `bearish_at` | `reversed_polarity` |
| --- | ---: | ---: | --- |
| `trend_50d` | `0.0` | `-0.05` | `False` |
| `trend_200d` | `0.0` | `-0.02` | `False` |
| `pct_from_52w_high` | `-0.10` | `-0.50` | `False` |
| `momentum_6m` | no entry | no entry | no entry |
| `realized_vol_90d` | `nan` | `1.50` | `False` |
| `max_drawdown_1y` | `nan` | `-0.60` | `False` |
| `avg_dollar_volume_20d` | `2_000_000` | `200_000` | `False` |
| `relative_volume` | no entry | no entry | no entry |
| `zero_volume_days_90d` | `1` | `5` | `True` |
| `share_dilution` | `0.03` | `0.15` | `True` |
| `cash_runway_months` | `24` | `6` | `False` |
| `filing_recency_days` | `nan` | `150` | `True` |
| `debt_to_assets` | `0.15` | `0.50` | `True` |
| `float_shares` | no entry | no entry | no entry |
| `days_to_earnings` | no entry | no entry | no entry |

Penny stance text injected into the research prompt and critic ground truth:

> This is a PENNY / MICRO-CAP stock (profile: penny). Operate with elevated skepticism: assume dilution and promotional activity until the filings show otherwise. Weigh survival metrics (cash runway, share dilution, filing recency, liquidity) above valuation metrics. News items may be paid promotion — treat headlines as claims, not facts. The bull case must clear a higher evidentiary bar than the bear case. Missing or stale SEC filings are themselves bearish evidence, not merely missing data.

**Confidence weights and caps**:

| Profile | Quote | Fundamentals | SEC financials | News | Macro | Scorecard | `dark_company_cap` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| largecap | `0.25` | `0.15` | `0.20` | `0.10` | `0.05` | `0.25` | `False` |
| penny | `0.25` | `0.10` | `0.30` | `0.0` | `0.0` | `0.35` | `True` |

For penny, news and macro have zero weight and therefore do not add confidence
or reason lines. If `dark_company_cap` is true and `data.financials is None`,
confidence is capped at `low` with reason
`"no SEC financials: dark-company cap low"`.

**Sizing bands**:

| Profile | High | Medium | Low | Liquidity sizing |
| --- | --- | --- | --- | --- |
| largecap | `(0.05, 0.10)` | `(0.03, 0.06)` | `(0.015, 0.03)` | `None` |
| penny | `(0.01, 0.03)` | `(0.005, 0.015)` | `(0.0025, 0.0075)` | `max_participation=0.10`, `max_days_to_exit=3` |

When liquidity sizing is present and `avg_dollar_volume_20d` is known:
`liquidity_cap_dollars = adv_dollars * max_participation * max_days_to_exit`.
`low_dollars` and `high_dollars` are capped at that value. If ADV is unknown,
no cap is applied, but the sizing note warns that liquidity is unknown.

**Optimizer exclusion rule.** The portfolio optimizer excludes any live holding
whose valuation price is `< 5.0` from the mean-variance symbol list and surfaces
`"excluded from mean-variance optimization: sample statistics on illiquid micro-caps are unreliable"`.
If fewer than two symbols remain after exclusion, the optimizer is skipped with
the same message. Correlation and the allocation backtest keep all symbols.

## 4. Transactions, realized P/L, and money-weighted return

The transaction ledger is an optional journal that applies changes to the
authoritative `holdings` table and recorded cash setting. It is not a replay
source: deleting a transaction deletes only that ledger row and does not reverse
cash or holdings effects.

Allowed sides are `buy`, `sell`, `deposit`, and `withdraw`. Dates must be ISO
calendar dates (`YYYY-MM-DD`) and cannot be in the future relative to UTC today.
Symbols are uppercased. Buy/sell rows require `symbol`, `shares > 0`, and
`price > 0`; deposit/withdraw rows require `amount > 0`. Buy/sell `amount` is
computed by code as `shares * price` rounded to cents with `Decimal` and
`ROUND_HALF_UP`; submitted buy/sell amount is ignored. Cash-flow amount is the
submitted amount rounded the same way.

**Buy application.** A buy first checks recorded cash. If
`cash_after = get_cash() - amount` is `< 0`, it raises
`ValueError("insufficient cash — record a deposit first or adjust cash")` before
writing anything. Otherwise cash becomes `cash_after`. Holdings use the
single-row average-cost method:

`new_shares = old_shares + shares`

When there is no old holding row, `avg_cost = price`. When an old row exists and
`old_avg_cost is not None`, the new basis is exactly:

`new_avg = (old_shares * old_avg_cost + shares * price) / new_shares`

When the old row's `avg_cost is None`, the new average stays `None`; unknown
basis plus known basis remains unknown.

**Sell application.** A sell requires an existing holding and rejects
`shares > held_shares + 1e-9`. Cash increases by `amount`. The holding's
`avg_cost` is unchanged while shares remain. If the remaining share count is
`< 1e-9`, the holding row is deleted. Realized P/L is stored only for sells:

`realized_pl = (price - avg_cost) * shares`

That result is rounded to cents with `Decimal` and `ROUND_HALF_UP`. If
`avg_cost is None`, realized P/L is stored as `None`.

**Money-weighted return.** `compute_returns()` uses only external cash flows:
deposits, withdrawals, and a terminal portfolio value. Buys and sells are
internal transfers because cash is inside the portfolio boundary, so they never
enter the MWR flow list. Flow signs are:

| Flow | XIRR amount |
| --- | ---: |
| Deposit | negative |
| Withdrawal | positive |
| Terminal value (`valuation.total_with_cash`) dated UTC today | positive |

`xirr()` annualizes by bisection over rates from `-0.9999` to `10.0`, with
maximum 200 iterations and a `1e-7` stopping tolerance on either absolute NPV
or rate interval. The net present value function is exactly:

`NPV(r) = sum(amount / (1 + r) ** (days_from_first / 365.25))`

`xirr()` returns `None` when there are fewer than two flows, all flows have the
same sign, the NPV at the two bounds does not change sign, or the span from the
first to last flow is less than 14 days. `compute_returns()` also returns MWR
`None` when no deposits/withdrawals have been recorded or when the current
valuation has unpriced symbols, because the terminal portfolio value is not
complete.

## 5. LLM research pipeline

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
across every **critic-chain** call — audit, revise, and re-critique (steps
2–4) — with an OpenRouter/Anthropic `CachePoint(ttl="1h")` inserted after it,
so those calls hit the cached-token price for that shared prefix instead of
paying full input price each time. The initial draft (step 1, `research_ticker`)
builds its own separate prompt from scratch and does not use `CachePoint` —
the shared-prefix caching only kicks in once the critic chain starts. This is
the main lever behind thorough mode's cost (§ Cost profile in the README)
staying in the $0.10–0.40 range rather than scaling linearly with the number
of calls.

**Discovery pipeline** (`app/llm/discovery.py::discover_ideas`). The plan model
selects one predefined Yahoo screen and/or thematic tickers, then code validates
each candidate against fetched market cap, P/E, quote availability, and sector.
When the goal names or clearly implies a sector, the prompt rule is:
"If the goal names or clearly implies a sector, set filters.sectors using
exactly these Yahoo sector names: Technology, Healthcare, Financial Services,
Consumer Cyclical, Consumer Defensive, Industrials, Energy, Basic Materials,
Real Estate, Utilities, Communication Services. Otherwise leave sectors null."
The model's advisory sector list is not trusted by itself: Python enforces it
against Yahoo's `sector` field, case-insensitively, and fails closed when a
sector-filtered candidate has no sector.

**Programmatic fabrication check** (`check_fabrication` in `app/llm/critic.py`).
Runs before every audit/re-critique call, independent of the LLM.
- **Scope** — scans report fields for numeric tokens via two different
  parsers. **Strict parse** (`key_metrics[*].value`, `valuation_context`):
  every numeric token counts, including bare integers and years. **Lenient
  prose parse** (`key_metrics[*].interpretation`, `indicator_view`, `summary`,
  `thesis.bull[*]`, `thesis.bear[*]`, `risks[*]`, `things_to_investigate[*]`):
  ignores bare integers 0–10 and 4-digit years 1900–2100 (so counts and dates
  in free text aren't flagged as figures), and also skips any token
  immediately followed by `-K` or `-Q` (case-insensitive) so a "10-K"/"10-Q"
  filing reference isn't parsed as the number 10. It also skips numbers used
  as period labels (`52-week`, `200 day`, `90d`, `52w`) or index-name prefixes
  (`S&P 500`). Un-$'d whole-number magnitude shorthand such as `6M` is treated
  as ambiguous and checked both as `6` and `6,000,000`; dollar amounts and
  decimal magnitudes such as `$228B` or `1.06B` stay magnitude-only.
- **Allowed set** — every number found in `fundamentals`, `quote`, SEC
  financials, macro context, indicator scorecard values, numbers parsed from
  indicator keys/labels/details, and news *headlines* (not article bodies, and
  never timestamps/URLs).
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

## 6. Confidence and sizing

`app/indicators/confidence.py::compute_confidence` builds a completeness score
by summing the active profile's weights (§3). The presence rules are stable
across profiles: quote present; at least 2 of the original 5 fundamentals
fields present (`market_cap`, `pe_ratio`, `forward_pe`, `profit_margin`,
`revenue`); SEC financials present; at least 3 news items; macro context
present; and scorecard `data_completeness × profile.confidence_weights.scorecard`.
Weights set to `0.0` are skipped entirely, including their reason line.

Completeness maps to a base grade: `≥0.75` → `high`, `≥0.45` → `medium`,
else `low`.

**Hard caps**, applied after the base grade, each only able to lower it:
- Any source with status `error` → capped at `medium`.
- No quote at all → capped at `low` (this cap can stack after the error cap,
  landing at `low` even if completeness alone would say `medium`).
- Penny profile only: no SEC financials and `dark_company_cap=True` → capped
  at `low`, with reason `"no SEC financials: dark-company cap low"`.

**Final clamp.** The report's own stated `confidence`, the critic's
`suggested_confidence`, and this computed grade are combined with
`clamp_confidence(*grades) = min(grades, key=order)` (`low < medium < high`)
in `research_ticker_cached`. The model can only ever pull confidence down from
what the data supports — never up.

**Position sizing** (`app/portfolio/decision_support.py::suggest_position_size`)
maps the final confidence to the active profile's starting-size band (§3), as
a fraction of investable portfolio value. The web research page passes holdings
value plus recorded cash (`total_with_cash`) as that base, so dry powder counts
when estimating a new position's dollar range.

For profiles with liquidity sizing, the web route passes the
`avg_dollar_volume_20d` indicator value into `suggest_position_size` as
`adv_dollars`. The cap formula is exactly:
`liquidity_cap_dollars = adv_dollars * max_participation * max_days_to_exit`.

If the symbol is already held, the existing securities-only weight is treated
as consumed headroom within the band: the suggested dollar range narrows to
`band − current_weight` (floored at 0), and if the current weight already
meets or exceeds the band's high end, the guidance switches to "already at
band — adding would increase concentration."

## 7. Portfolio math

**Valuation aggregation** (`app/portfolio/holdings.py::value_holdings`).
`total_value`, `total_cost`, and `total_unrealized_pl` are summed **only**
over holdings that priced successfully this call; a holding with no fetchable
quote contributes nothing to any total and its symbol is listed in
`unpriced_symbols` (it's still shown in the table, with `market_value: null`).
Cost/P&L additionally require `avg_cost` to have been recorded — omit it and
that holding is excluded from cost/P&L totals even when priced. Per-holding
`weight` is `market_value / total_value` for every *priced* holding as soon
as `total_value > 0` — an unpriced holding leaves only its own weight `null`
and does not block the others from getting one. These per-holding weights
are what the Health and Allocation Backtest panels consume; Correlation
instead takes the raw holdings symbol list and fetches its own independent
return history, without reference to weight at all.

Cash is stored separately in SQLite settings as a single non-negative dollar
amount. `value_holdings` exposes it as `cash`, `total_with_cash`
(`total_value + cash`), and `cash_pct` (`cash / total_with_cash` when positive).
These fields do **not** change `total_value` or per-holding `weight`, so the
Health, Correlation, Allocation Backtest, and Optimizer panels keep their
existing securities-only allocation semantics.

**Target allocations & rebalance plan** (`app/portfolio/plan.py`). User target
weights are stored as fractions (`0.25` means 25%) in the `targets` SQLite
table. Saving targets is a full replacement: every weight must be finite and
between `0` and `1`, and the sum of all target weights must be `<= 1.0 + 1e-6`.
Any unallocated remainder is the implicit cash target:

```
cash_target_weight = max(0, 1 - sum(target_weight))
```

The `max(0, ...)` clamp only matters for the `1e-6` validation tolerance near
100%; ordinary under-allocation is shown as the implicit cash target.

`plan_rebalance(valuation, targets)` is deterministic and does no I/O. It
returns `None` when `valuation.total_with_cash <= 0` or when there are no
targets. Its base value is always:

```
base_value = valuation.total_with_cash
```

For every symbol with a target row, current weight is recomputed over that base
instead of reusing `HoldingValuation.weight`:

```
current_weight = market_value / base_value
drift = current_weight - target_weight
```

Held symbols with no target row are listed in `untargeted` and excluded from
trade suggestions; this distinction matters because an explicit `0%` target
does produce a sell suggestion. If `abs(drift) <= DRIFT_THRESHOLD` (currently
5%, imported from `decision_support.py`), the item is a hold and both deltas are
zero. Otherwise:

```
delta_usd = round((target_weight - current_weight) * base_value, 2)
delta_shares = round(delta_usd / price, 4)
after_weight = (market_value + delta_usd) / base_value
```

Positive `delta_usd` is a buy; negative is a sell. Hold items keep
`after_weight = current_weight`. If a targeted symbol has no price, `delta_usd`
is still computed but `delta_shares` is `null`. The final cash estimate is:

```
cash_after = cash_now - sum(delta_usd)
```

Items are sorted by absolute drift descending so the largest gaps appear first.

**Contribution-only plan** (`plan_contribution`). A what-if contribution preview
is deterministic and buys only with the new contribution amount; it never sells
existing positions. It returns `None` when `contribution <= 0`, when
`valuation.total_with_cash <= 0`, or when there are no targets. The post-cash
base is:

```
base_after = valuation.total_with_cash + round(contribution, 2)
```

For each target symbol:

```
current_weight = current_value / base_after
deficit = max(0, target_weight * base_after - current_value)
```

If `sum(deficit) <= contribution`, each buy is its full deficit rounded to
cents. Otherwise each buy is the proportional share of the contribution,
rounded to cents:

```
buy_usd = round(contribution * deficit / sum(deficit), 2)
```

If rounding would put the total above the contribution, the largest buy is
reduced by the overage. Buys below `$1.00` are dropped. `buy_shares` is
`round(buy_usd / price, 4)` when price is positive, else `null`. After-trade
weight is:

```
after_weight = (current_value + buy_usd) / base_after
```

Items are sorted by `buy_usd` descending, and leftover cash is:

```
leftover_cash = round(contribution - sum(buy_usd), 2)
```

**NAV history** (`app/portfolio/snapshots.py`). Each `GET /portfolio` attempts
to write one daily NAV snapshot for the current UTC date after live valuation
finishes, and the in-process daily job attempts the same write at the configured
UTC hour. The row key is `day` (`YYYY-MM-DD` UTC), and writes use
`INSERT OR REPLACE`, so the latest successful page visit or daily job for the
UTC day wins. A snapshot is skipped when any holding is unpriced
(`unpriced_symbols` non-empty) or when `total_with_cash <= 0`; the app never
persists a partially-priced or zero-value NAV. The NAV series is actual account
history over recorded snapshots (`total_with_cash`, securities plus cash),
unlike the constant-weight Allocation Backtest below.

**Allocation backtest** (`app/portfolio/performance.py::compute_performance`).

> **This is not the account's realized return.** It replays *today's* live
> weights, held constant, over the lookback window (730 days / ~2 years by
> default) against a benchmark (SPY by default) using `quantstats_lumi`. It
> answers "what would CAGR/Sharpe/Sortino/volatility/max-drawdown have been if
> I'd held this exact allocation the whole time" — not what this account
> actually earned, since real holdings and weights changed over that period
> and no transaction history feeds this calculation.

Requires ≥30 days of overlapping portfolio return history or it returns
`None` (no metrics shown). When symbols are excluded for insufficient history,
the remaining weights are renormalized to sum to 100% before computing the
constant-weight return series, and excluded symbols are listed in
`excluded_symbols`. Benchmark CAGR is computed only over the date intersection
with the portfolio series, also gated at ≥30 common rows.

**Optimizer** (`app/portfolio/optimize.py::optimize`). Mean-variance
optimization via `skfolio.MeanRisk` with `RiskMeasure.VARIANCE`. `max_sharpe`
maps to `MAXIMIZE_RATIO`, `min_risk` to `MINIMIZE_RISK`. `risk_free_rate`
(0–20%, default 0) feeds both skfolio's objective and the independently
computed Sharpe (`(annualized return − rf) / annualized volatility`, using
sample mean × 252 and sample covariance × 252 — no shrinkage).

- **Penny-price exclusion at the web route**: before calling `optimize()`, the
  portfolio route removes any live holding whose valuation price is `< 5.0`.
  The warning text is
  `"excluded from mean-variance optimization: sample statistics on illiquid micro-caps are unreliable"`.
  If fewer than two symbols remain after exclusion, the optimizer is skipped
  with the same message. Correlation and allocation backtest keep all symbols.
- **Per-asset cap**: `max_weight` (default 0.35) is relaxed to `1/n` if the
  requested cap is infeasible for `n` assets (e.g. 4 assets can't each stay
  under 20%); a warning records the relaxation.
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

## 8. Known limitations

- **No outcome tracking.** Nothing in this codebase records what a report
  predicted against what actually happened later — there is no calibration
  loop scoring past confidence or theses against subsequent price action.
- **No lot-level cost basis.** `upsert_holding` overwrites `shares`/`avg_cost`
  on conflict; adding to a position at a new price replaces the average
  rather than tracking individual tax lots.
- **Sparse NAV history by design.** NAV snapshots are app-generated records,
  not broker-sourced transaction history. The daily job fills days without a
  portfolio page visit only while the app process is running, and a
  partially-priced valuation is deliberately skipped rather than stored.
- **Fabrication check is magnitude-only**, not semantic (see §5) — it can
  match a fabricated number to an unrelated real figure that happens to be
  numerically close.
- **SEC comparative periods are not guaranteed to be exactly annual.**
  `share_dilution` uses the oldest and newest available SEC share-count period
  columns; those are usually roughly one fiscal year apart but not guaranteed.
  The indicator detail names both periods.
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
