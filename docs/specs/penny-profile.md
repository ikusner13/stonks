# Spec: Research Profiles (largecap / penny)

One codebase, two research profiles. A **profile** is a frozen policy object that owns
everything that varies by asset class: which indicators are computed, their thresholds,
confidence weights and caps, position-sizing bands, LLM prompt stance, and optimizer
eligibility. All profile-varying *policy* lives in `app/profiles/`; the rest of the app
becomes profile-parameterized *mechanism*. That is the code split.

**Non-negotiable acceptance criterion: the largecap profile reproduces current behavior
byte-for-byte** — same 12 indicators, same thresholds, same confidence weights, same
sizing bands, same prompts (empty stance string). Every existing test passes unmodified
except for call-signature updates.

---

## 1. New package `app/profiles/`

### `app/profiles/base.py`

```python
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Literal, Mapping

from ..schemas import Confidence

ProfileKey = Literal["largecap", "penny"]

@dataclass(frozen=True)
class Threshold:
    bullish_at: float                 # math.nan = never bullish
    bearish_at: float                 # math.nan = never bearish
    reversed_polarity: bool = False   # True: value < bullish_at → bullish, value > bearish_at → bearish

@dataclass(frozen=True)
class ConfidenceWeights:
    quote: float
    fundamentals: float               # awarded when ≥2 of 5 known fields present
    financials: float
    news: float                       # awarded when ≥3 items
    macro: float
    scorecard: float                  # multiplied by scorecard.data_completeness
    dark_company_cap: bool = False    # financials is None → hard-cap confidence at "low"

@dataclass(frozen=True)
class LiquiditySizing:
    max_participation: float          # fraction of avg daily dollar volume per day
    max_days_to_exit: int             # position must be exitable in this many days

@dataclass(frozen=True)
class Profile:
    key: ProfileKey
    label: str
    indicator_keys: tuple[str, ...]                       # ordered; engine computes exactly these
    thresholds: Mapping[str, Threshold]                    # keys ⊆ indicator_keys; absent key = informational (always neutral)
    confidence_weights: ConfidenceWeights
    sizing_bands: Mapping[Confidence, tuple[float, float]] # (low_pct, high_pct) of portfolio
    liquidity_sizing: LiquiditySizing | None               # None = no liquidity cap on sizing
    optimizer_included: bool                               # False → symbols on this profile excluded from mean-variance
    research_stance: str                                   # prepended to ground truth; "" = no prompt change
```

### `app/profiles/__init__.py`

```python
PROFILES: dict[ProfileKey, Profile] = {"largecap": LARGECAP, "penny": PENNY}

PENNY_PRICE_MAX = 5.0
PENNY_MARKET_CAP_MAX = 75_000_000
OTC_EXCHANGES = {"PNK", "OTC", "OEM", "OQB", "OQX"}   # yfinance `exchange` codes for OTC tiers

def select_profile(data: TickerData, override: ProfileKey | None = None) -> tuple[Profile, str]:
    """Deterministic profile selection. Returns (profile, human-readable reason).

    Rules, first match wins:
      1. override given                       → that profile, reason "manual override"
      2. fundamentals.exchange in OTC set     → penny,   "OTC-listed ({exchange})"
      3. quote.price < 5.0                    → penny,   "price ${price} < $5"
      4. fundamentals.market_cap < 75e6       → penny,   "market cap ${cap} < $75M"
      5. otherwise (incl. all fields missing) → largecap, "default"
    """
```

### `app/profiles/largecap.py`

`LARGECAP` — **verbatim** current behavior:
- `indicator_keys`: the current 12, in the current order: `momentum_12_1, momentum_6m,
  pct_from_52w_high, trend_200d, realized_vol_90d, beta_1y, max_drawdown_1y,
  earnings_yield, fcf_yield, profit_margin, debt_to_assets, days_to_earnings`.
- `thresholds`: exactly the current `_RULES` table from `app/indicators/engine.py`
  (`debt_to_assets` gets `reversed_polarity=True`; `beta_1y`/`days_to_earnings` have no
  entry — informational).
- `confidence_weights`: `quote=0.25, fundamentals=0.15, financials=0.20, news=0.10,
  macro=0.05, scorecard=0.25, dark_company_cap=False`.
- `sizing_bands`: `high (0.05, 0.10), medium (0.03, 0.06), low (0.015, 0.03)`.
- `liquidity_sizing=None`, `optimizer_included=True`, `research_stance=""`.

### `app/profiles/penny.py`

`PENNY`:

- `indicator_keys` (15, this order):
  `trend_50d, trend_200d, pct_from_52w_high, momentum_6m, realized_vol_90d,
  max_drawdown_1y, avg_dollar_volume_20d, relative_volume, zero_volume_days_90d,
  share_dilution, cash_runway_months, filing_recency_days, debt_to_assets,
  float_shares, days_to_earnings`

- `thresholds` (absent key = informational/neutral):

  | key | Threshold | rationale |
  | --- | --- | --- |
  | `trend_50d` | `(0.0, -0.05)` | pennies often lack 200d history; 50d trend is the actionable one |
  | `trend_200d` | `(0.0, -0.02)` | unchanged |
  | `pct_from_52w_high` | `(-0.10, -0.50)` | wider: pennies live further from highs; −50% = dilution-drift flag |
  | `momentum_6m` | *no entry* (informational) | micro-cap momentum premium fails/reverses; context only |
  | `realized_vol_90d` | `(nan, 1.50)` | re-baselined: >150% annualized is extreme even for pennies |
  | `max_drawdown_1y` | `(nan, -0.60)` | re-baselined |
  | `avg_dollar_volume_20d` | `(2_000_000, 200_000)` | tradability: >$2M/day bullish, <$200k/day bearish |
  | `zero_volume_days_90d` | `(1, 5, reversed_polarity=True)` | 0 zero-volume days bullish, >5 bearish |
  | `share_dilution` | `(0.03, 0.15, reversed_polarity=True)` | <3% share growth bullish, >15% bearish |
  | `cash_runway_months` | `(24, 6)` | >24mo runway bullish, <6mo bearish |
  | `filing_recency_days` | `(nan, 150, reversed_polarity=True)` | never bullish; >150d since period end = stale filer. `value < nan` is False in Python, so nan naturally means "never" in the reversed branch too — no special-casing |
  | `debt_to_assets` | `(0.15, 0.50, reversed_polarity=True)` | unchanged; toxic-debt matters here |
  | `relative_volume`, `float_shares`, `days_to_earnings` | *no entry* | context: catalyst confirmation, squeeze/volatility mechanics, event risk |

- `confidence_weights`: `quote=0.25, fundamentals=0.10, financials=0.30, news=0.0,
  macro=0.0, scorecard=0.35, dark_company_cap=True` (sums to 1.0). News weight is
  deliberately zero: penny news flow is promotion-dominated, so its *presence* must not
  raise confidence. `dark_company_cap`: no SEC financials → cap `low` with reason
  `"no SEC financials: dark-company cap low"` — a non-filer is uninvestable-grade
  information regardless of what else is present.
- `sizing_bands`: `high (0.01, 0.03), medium (0.005, 0.015), low (0.0025, 0.0075)`.
- `liquidity_sizing`: `LiquiditySizing(max_participation=0.10, max_days_to_exit=3)`.
- `optimizer_included=False`.
- `research_stance` (exact text):

  > This is a PENNY / MICRO-CAP stock (profile: penny). Operate with elevated
  > skepticism: assume dilution and promotional activity until the filings show
  > otherwise. Weigh survival metrics (cash runway, share dilution, filing recency,
  > liquidity) above valuation metrics. News items may be paid promotion — treat
  > headlines as claims, not facts. The bull case must clear a higher evidentiary bar
  > than the bear case. Missing or stale SEC filings are themselves bearish evidence,
  > not merely missing data.

---

## 2. Data layer

### `app/schemas.py`

- `Fundamentals` gains explicit fields (all `float | str | None = None` as typed):
  `exchange: str | None`, `float_shares: float | None`, `shares_outstanding: float | None`.
- `ResearchResult` gains `profile: str = "largecap"` and `profile_reason: str = ""`
  (defaults keep previously-cached blobs valid — extend `test_research_result_compat`).

### `app/data/yahoo.py::fetch_fundamentals`

From the same `info` dict already fetched: `exchange` ← `info["exchange"]`,
`float_shares` ← `info["floatShares"]`, `shares_outstanding` ← `info["sharesOutstanding"]`.
No new network calls. (`_is_empty` in `app/data/__init__.py` keeps checking only the
original 5 fields — an exchange code alone must not flip `fundamentals` to `ok`.)

### `app/data/sec.py`

- `SecFinancials` gains `shares_outstanding_prior: float | None = None` and
  `prior_period: str | None = None`.
- In `_fetch_blocking`, balance sheet: `val_cols` already lists all period columns;
  today only `val_cols[0]` is read. Add: read the **last** (oldest) period column for
  `SharesYearEnd` → `shares_outstanding_prior`, and record that column's label as
  `prior_period`. Only set when ≥2 period columns exist and the value is non-null.
- `numeric_values()` includes the new numeric field (keeps the fabrication-check
  allowed-set complete).
- Known limitation (goes in methodology): comparative periods are usually ~1 fiscal
  year apart but not guaranteed; `share_dilution.detail` must name both periods.

---

## 3. Indicator engine (`app/indicators/`)

### `app/indicators/schemas.py`

- `Unit = Literal["pct", "ratio", "days", "usd", "count"]`.
- `IndicatorScorecard` gains `profile: str = "largecap"`. `data_completeness` already
  divides by `len(indicators)` — no change.

### `app/indicators/engine.py` refactor

- Delete module-level `_RULES`. `_signal(profile, key, value)` reads
  `profile.thresholds`; missing key → `"neutral"` (informational). Reversed polarity
  branch per `Threshold.reversed_polarity`.
- Restructure into a **builder registry**: each indicator is one function taking a
  shared context; the engine iterates `profile.indicator_keys`:

```python
@dataclass
class IndicatorContext:
    close: pd.Series | None
    volume: pd.Series | None          # NEW — same index as close
    spy_close: pd.Series | None
    data: TickerData
    days_to_earnings: int | None
    profile: Profile

Builder = Callable[[IndicatorContext], Indicator]
BUILDERS: dict[str, Builder] = { ... }   # every key either profile may reference

def compute_indicators(ctx: IndicatorContext) -> list[Indicator]:
    return [BUILDERS[key](ctx) for key in ctx.profile.indicator_keys]
```

- Existing 12 builders: mechanical extraction of current code, unchanged math,
  thresholds now read via `_signal(profile, ...)`.
- `_fetch_history` also extracts the `Volume` column (yf.download already returns it;
  mirror `_extract_close` for volume, symbol only — SPY volume not needed). Return shape:
  `{"close": dict[str, Series], "volume": dict[str, Series]}`.

**New builders** (all `unavailable` with a stated reason when inputs are missing,
matching the existing pattern; values `_round`ed):

| key | label | unit | formula | min data |
| --- | --- | --- | --- | --- |
| `trend_50d` | Price vs 50d trend | pct | `close[-1] / mean(close[-50:]) - 1` | 50 closes |
| `avg_dollar_volume_20d` | Avg daily dollar volume (20d) | usd | `mean(close[-20:] * volume[-20:])` | 20 aligned close+volume rows |
| `relative_volume` | Relative volume (today vs 20d) | ratio | `volume[-1] / mean(volume[-21:-1])`; unavailable if denominator is 0 | 21 volume rows |
| `zero_volume_days_90d` | Zero-volume days (90d) | count | `int((volume[-90:] == 0).sum())` | 90 volume rows |
| `share_dilution` | Share dilution (period over period) | pct | `shares_outstanding / shares_outstanding_prior - 1` (both from SEC); detail names `prior_period` → `fiscal_period` | both present, prior > 0 |
| `cash_runway_months` | Cash runway | ratio | if `operating_cash_flow < 0`: `cash_and_equivalents / (abs(operating_cash_flow) / 12)` (OCF is trailing-annual XBRL). If `operating_cash_flow >= 0`: `value=None, signal="bullish", detail="operating cash flow positive — self-funding"` (same forced-signal pattern as `earnings_yield`'s negative-earnings branch) | cash + OCF present |
| `filing_recency_days` | Days since last SEC period end | days | `(today_utc - date(financials.filed)).days` | `filed` parseable |
| `float_shares` | Float | count | `fundamentals.float_shares`, detail in millions | present |

- `compute_scorecard(symbol, data, *, profile, fresh=False)`: cache key becomes
  `f"{sym}:{profile.key}:{date}"` (namespace `scorecard` unchanged); passes profile into
  the context; sets `IndicatorScorecard.profile = profile.key`. Update module docstring
  (no longer "12 indicators").

### `app/indicators/confidence.py`

`compute_confidence(data, scorecard, profile)`:
- Replace hardcoded weights with `profile.confidence_weights` (same presence rules:
  fundamentals ≥2 of the 5 original fields — the 3 new fields do **not** count;
  news ≥3 items; reasons strings unchanged in shape). Skip the reason line entirely for
  any weight set to 0.0 (penny: no news/macro lines).
- Grade cut points unchanged (`≥0.75 high, ≥0.45 medium`).
- Existing caps unchanged (any source `error` → medium; no quote → low). New, after
  them: `if profile.confidence_weights.dark_company_cap and data.financials is None:`
  cap `low`, append reason above.

---

## 4. LLM pipeline (`app/llm/`)

- `research.py::research_ticker(symbol, data, scorecard, profile)`: when
  `profile.research_stance` is non-empty, insert a `PROFILE CONTEXT:` block between the
  ground-truth JSON and the closing instruction. Empty stance → prompt byte-identical
  to today. `SYSTEM` prompts and singleton agents untouched (stance rides in the user
  prompt, not the system prompt, so agent caching is unaffected).
- `critic.py::_ground_truth(data, scorecard, profile)`: when stance non-empty, prepend
  `PROFILE CONTEXT: {stance}\n\n` to the returned string. It's inside the shared
  cacheable prefix, before the `CachePoint` — constant across the audit/revise/
  re-critique chain, so prefix caching still works. Thread `profile` through
  `research_ticker_reviewed` and `critique_report`.
- `pipeline.py::research_ticker_cached(symbol, mode, *, profile_override: ProfileKey | None = None, fresh=False)`:
  1. fetch ticker data (unchanged),
  2. `profile, reason = select_profile(ticker, profile_override)`,
  3. report cache key becomes `f"{sym}:{day}:{mode}:{profile.key}"` (override can change
     the profile for the same symbol/day, so the key must carry it),
  4. pass profile to `compute_scorecard`, `compute_confidence`,
     `research_ticker_reviewed`,
  5. set `ResearchResult.profile = profile.key`, `profile_reason = reason`.
- Fabrication check: no changes needed — new indicator values arrive via
  `scorecard.numeric_values()`, new fundamentals/SEC fields via the existing
  `model_dump()` / `numeric_values()` paths.
- Discovery: unchanged this iteration.

---

## 5. Portfolio (`app/portfolio/`)

### `decision_support.py::suggest_position_size`

New signature:

```python
def suggest_position_size(
    portfolio_value: float,
    confidence: Confidence,
    symbol: str | None = None,
    *,
    current_weight: float | None = None,
    profile: Profile = LARGECAP,          # default preserves every existing caller/test
    adv_dollars: float | None = None,     # avg_dollar_volume_20d indicator value, if known
) -> PositionSizeGuidance
```

- Bands from `profile.sizing_bands` (module-level `_SIZE_BANDS` becomes the largecap
  profile's table).
- When `profile.liquidity_sizing` is set and `adv_dollars` is not None:
  `liquidity_cap_dollars = adv_dollars * max_participation * max_days_to_exit`;
  `low_dollars`/`high_dollars` are additionally capped at it. If the cap binds
  (`liquidity_cap_dollars < high-band dollars`), the note must state the constraint in
  plain language ("sized so the position could be exited in ~3 days at 10% of average
  daily dollar volume").
- When `profile.liquidity_sizing` is set but `adv_dollars` is None: no cap, but the
  note appends a warning that liquidity is unknown and the upper band should be
  treated with caution.
- `PositionSizeGuidance` gains `profile: str = "largecap"`,
  `liquidity_cap_dollars: float | None = None`.
- Web caller passes `profile` + the `avg_dollar_volume_20d` value pulled from the
  research result's scorecard (`next((i.value for i in scorecard.indicators if i.key == "avg_dollar_volume_20d"), None)`).

### Optimizer exclusion

In the portfolio-page assembly (web route that builds the `optimize()` symbol list):
exclude any holding whose live valuation price is `< PENNY_PRICE_MAX` (price is already
fetched for valuation — no extra requests; full profile selection would need
fundamentals per holding, deliberately avoided). Excluded symbols surface in the
optimizer panel as a warning: *"excluded from mean-variance optimization: sample
statistics on illiquid micro-caps are unreliable"*. If <2 symbols remain, skip the
optimizer with the same message. Correlation and the allocation backtest keep all
symbols (their existing history filters already handle sparse series).

---

## 6. Surfaces

- **Web** (`app/web/`): research route accepts `?profile=penny|largecap` (validated;
  else 422) → `profile_override`. Report page shows a profile badge (key + reason)
  near the confidence chip. Scorecard table already renders the indicator list
  dynamically — verify `usd`/`count` units format sanely (usd → `$1.2M` style,
  count → integer).
- **CLI** (`app/cli.py`): `stocks research SYM --profile penny` → same override.
- No config/env changes.

---

## 7. Docs (required — hard rule)

`docs/methodology.md`: new §"Profiles" (selection rules, exact both-profile threshold
tables, per-profile confidence weights + dark-company cap, per-profile sizing bands +
liquidity-cap formula, optimizer exclusion rule, stance text). Update §2's "12
indicators" phrasing, §4 weight table, §5 sizing. `README.md`: one feature-tour bullet.

---

## 8. Tests (acceptance criteria)

1. **Largecap regression**: all existing tests pass with only call-signature updates
   (no expected-value changes). Explicit test: largecap prompt strings are
   byte-identical to the pre-change format (empty stance adds nothing).
2. `tests/test_profiles.py`: each `select_profile` rule + precedence + override +
   all-fields-missing → largecap.
3. New-builder unit tests with synthetic series: dollar volume, relative volume
   (incl. zero-denominator → unavailable), zero-volume days, dilution (incl. missing
   prior → unavailable), runway (negative OCF math; OCF ≥ 0 → forced bullish with null
   value; missing cash → unavailable), filing recency, trend_50d, float_shares;
   plus penny thresholds: vol 1.4 → neutral, 1.6 → bearish; dilution 0.20 → bearish;
   runway 4 → bearish.
4. Confidence: penny weights sum/grades; news count does not move penny completeness;
   dark-company cap fires only when `financials is None` and only for penny; largecap
   numbers unchanged.
5. Sizing: penny bands; liquidity cap binds and appears in note + field; adv None →
   warning, no cap; largecap unchanged.
6. Optimizer exclusion: sub-$5 holding excluded with warning; <2 remaining → skipped.
7. Compat: legacy `ResearchResult` blob without `profile` fields validates (defaults).
8. Scorecard cache keys for the two profiles don't collide (`SYM:penny:date` vs
   `SYM:largecap:date`); report key carries profile.
9. `uv run pytest -q` and `uv run ruff check` clean.

---

## 9. Out of scope (explicitly deferred)

- OTC Markets tier API (QX/QB/Pink Current/Limited/Expert) — selection uses yfinance
  exchange codes for now.
- Promotion/pump detector (PR-count × volume-anomaly), SEC full-text going-concern &
  shelf-offering (S-1/424B) parsing, trading suspensions, short interest / DTC.
- Price-action setup detection (VCP/consolidation-breakout, EMA-stack,
  undercut-and-reclaim) — trading-execution tooling, not research reporting;
  `relative_volume` / `avg_dollar_volume_20d` / `float_shares` cover the scanner-style
  basics deterministically.
- Discovery changes, benchmark change (IWM), backtest changes, multi-period dilution
  series.

## 10. Suggested orchestration split

Phases for a Workflow run; 1–2 gate 3–6, which are then independent; 7 last.

1. `app/profiles/` package + `select_profile` + tests (pure, no I/O).
2. Schema/data additions: `Fundamentals`, `SecFinancials`, yahoo/sec fetchers,
   `ResearchResult` fields + compat test.
3. Engine refactor to builder registry + volume plumbing + new builders + tests.
4. Confidence + sizing profile-parameterization + tests.
5. LLM plumbing (stance injection, cache keys, pipeline threading).
6. Web/CLI surfaces + optimizer exclusion.
7. Docs pass (methodology, README) + full-suite verification.
