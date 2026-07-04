# Spec: Call Ledger (outcome tracking)

**Goal.** Every research report is an implicit market call. Record each one at
generation time, then score it against realized forward returns (vs SPY) so the
user can see whether the pipeline's views — and its stated confidence — are
worth anything. Code computes every number; there are **zero new LLM call
sites**. The ledger is append-only: calls are recorded once, outcomes are
written once when they mature, nothing is ever revised.

**Non-goals (v1).** No scoring of discovery candidates. No position sizing / PnL
simulation. No LLM interpretation of the ledger. No per-field report grading —
the unit is the report's overall stance.

---

## 1. Report schema change — explicit stance

The report currently has bull/bear theses and a confidence, but no directional
view. Deriving direction in code from thesis counts would be code inventing
judgment; instead the LLM states it. Add to `TickerReport` in `app/schemas.py`:

```python
Stance = Literal["bullish", "neutral", "bearish"]

class TickerReport(BaseModel):
    ...
    stance: Stance | None = Field(
        default=None,
        description=(
            "The directional view implied by this report over roughly the next "
            "quarter, weighing the bull and bear cases: bullish, neutral, or "
            "bearish. Derived only from the provided data."
        ),
    )
```

`default=None` (not `"neutral"`) so cached pre-change reports still validate
and are distinguishable from a genuine neutral call. Export `Stance` from
`app.schemas`. No prompt changes needed — Pydantic AI feeds the field
description to the model. The critic schema is untouched.

## 2. Storage — two tables in the existing SQLite DB

Extend `init_db()` in `app/db.py` (`CREATE TABLE IF NOT EXISTS`, no migration
needed):

```sql
CREATE TABLE IF NOT EXISTS calls (
    id         INTEGER PRIMARY KEY,
    symbol     TEXT NOT NULL,
    as_of      TEXT NOT NULL,              -- UTC trading day of the report (matches pipeline key)
    mode       TEXT NOT NULL,              -- 'thorough' | 'cheap'
    stance     TEXT,                        -- NULL for pre-stance backfilled reports
    confidence TEXT NOT NULL,               -- 'low' | 'medium' | 'high'
    price      REAL,                        -- quote price at report time, display only
    revised    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, as_of, mode)
);

CREATE TABLE IF NOT EXISTS call_outcomes (
    call_id      INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    horizon      TEXT NOT NULL,             -- '1w' | '1m' | '3m'
    fwd_return   REAL NOT NULL,             -- symbol adj-close return over the window
    bench_return REAL NOT NULL,             -- SPY adj-close return, same window
    evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (call_id, horizon)
);
```

`price` is for display; **returns are computed leg-to-leg from adjusted
closes** (see §4), never mixing the intraday quote with adjusted closes.

## 3. Module `app/ledger.py` — public interface

```python
from datetime import date
from typing import Literal

from pydantic import BaseModel

from .schemas import Confidence, ResearchResult, Stance

Horizon = Literal["1w", "1m", "3m"]
HORIZON_DAYS: dict[Horizon, int] = {"1w": 7, "1m": 30, "3m": 91}  # calendar days
BENCHMARK = "SPY"


class Outcome(BaseModel):
    horizon: Horizon
    fwd_return: float
    bench_return: float

    @property
    def excess(self) -> float: ...        # fwd_return - bench_return


class Call(BaseModel):
    id: int
    symbol: str
    as_of: str                            # ISO date
    mode: str
    stance: Stance | None
    confidence: Confidence
    price: float | None
    revised: bool
    outcomes: dict[Horizon, Outcome]      # only matured+evaluated horizons present


class LedgerSummary(BaseModel):
    total_calls: int
    scored_calls: int                     # calls with ≥1 outcome
    # keyed by horizon; a bucket is absent when it has no data
    hit_rate: dict[Horizon, float]        # directional calls only (see hit rule)
    avg_excess: dict[Horizon, float]      # ALL calls with that outcome, incl. neutral/None
    n_directional: dict[Horizon, int]     # denominator behind hit_rate
    # calibration: hit rate at 1m by stated confidence, directional calls only
    hit_rate_by_confidence: dict[Confidence, float]


def record_call(result: ResearchResult, mode: str) -> None:
    """INSERT OR IGNORE a row derived from a freshly generated report.
    as_of = the UTC date in result.ticker.fetched_at's day (same day the
    pipeline keyed the report by); price = result.ticker.quote.price if set."""


async def evaluate_pending() -> int:
    """Compute and insert outcomes for every (call, horizon) that has matured
    and has no row yet. One batched yfinance download. Returns rows inserted.
    Network/API failure inserts nothing and raises nothing (log and return 0) —
    scoring must never break a page load."""


def list_calls(limit: int = 200) -> list[Call]:
    """Newest first, outcomes attached."""


def summarize(calls: list[Call]) -> LedgerSummary:
    """Pure aggregation, no IO."""
```

**Hit rule** (encode exactly): a call is *directional* iff `stance` is
`bullish` or `bearish`. `hit = excess > 0` for bullish, `excess < 0` for
bearish. Neutral and `stance IS NULL` calls are excluded from `hit_rate` and
`hit_rate_by_confidence` but included in `avg_excess`. Ties (`excess == 0`)
count as misses.

## 4. Scoring algorithm (`evaluate_pending`)

1. **Pending set** — SQL: all `(call, horizon)` pairs where
   `date(as_of, '+' || days || ' days') < date('now')` and no
   `call_outcomes` row exists. If empty, return 0 without any network call.
2. **One batch download** — `yf.download(symbols + [BENCHMARK],
   start=min(as_of), auto_adjust=True, progress=False, group_by="column")`,
   run via `asyncio.to_thread` (yfinance is sync — same pattern as
   `app/portfolio/performance.py:_fetch_returns`). Use the `Close` frame
   (adjusted, given `auto_adjust=True`).
3. **Per pair** — pure function so it's unit-testable without network:

   ```python
   def score_window(closes: pd.Series, as_of: date, days: int) -> float | None:
       """Return close-to-close return from the first trading close ON/AFTER
       as_of to the first trading close ON/AFTER as_of+days. None if either
       leg has no bar yet (not matured / symbol missing) — caller skips, the
       pair stays pending."""
   ```

   Compute `fwd_return` from the symbol series and `bench_return` from the
   SPY series **over the identical date logic**; insert only when both legs
   resolve. Delisted/renamed symbols simply never resolve — acceptable; they
   surface as scored=0 rows in the UI, not errors.
4. Insert with `INSERT OR IGNORE` (PK makes re-evaluation idempotent).

Weekend/holiday `as_of` therefore bases at the next trading close — consistent
for both legs, so excess is fair.

## 5. Recording hook

In `app/llm/pipeline.py::research_ticker_cached`, after `with_cache` returns:
call `record_call(result, mode)` **only when `hit` is False** (fresh
generation). The DB `UNIQUE` makes accidental double-recording harmless, but
cache hits must not touch the ledger at all. A `record_call` failure must not
break research (wrap, log).

## 6. Surfaces

**Web** (`app/web/app.py` + templates, follow the existing HTMX pattern —
skeleton page then partial):

- `GET /ledger` — page shell with nav entry ("Ledger"), renders immediately.
- `GET /ledger/table` — HTMX partial; `await evaluate_pending()` first, then
  render summary tiles + calls table. Tiles: total/scored counts, hit rate and
  avg excess per horizon (with directional-N shown, e.g. "67% · n=12"),
  hit-rate-by-confidence row. Table columns: date, symbol (links to
  `/research/{symbol}`), mode, stance, confidence, price, then per horizon
  `fwd / excess` with hit ✓/✗ coloring; em-dash for unmatured. Neutral/NULL
  stance rows show returns but no ✓/✗.

**CLI** (`app/cli.py`):

- `stocks ledger` — runs `evaluate_pending`, prints the summary block and the
  most recent 20 calls (rich table, match existing CLI style).
- `stocks ledger-backfill` — seeds history: scan `.cache/report/*.json`,
  parse each entry's `value` as `ResearchResult` (ignore `expiresAt` — expired
  files are exactly the history we want), extract `symbol:day:mode` from the
  filename, `record_call` each. Skip unparseable files with a warning count.
  Old reports have `stance=None` and still contribute to avg-excess tracking.

## 7. Tests (`tests/test_ledger.py`)

Pure logic tested without network or LLM:

- `score_window`: exact-date base, weekend roll-forward, unmatured → `None`,
  missing symbol column → `None`.
- Hit rule: bullish/bearish × positive/negative excess, tie → miss,
  neutral & NULL excluded from hit rate but present in avg_excess.
- `summarize`: buckets absent when empty; confidence calibration uses 1m only.
- `record_call` idempotency: same (symbol, as_of, mode) twice → one row
  (in-memory/`tmp_path` SQLite via `STOCKS_DB_PATH`).
- `evaluate_pending` with a monkeypatched download returning a fixture frame:
  inserts matured pairs only, second run inserts 0.

## 8. Acceptance criteria

1. `uv run stocks research AAPL` (fresh) → one `calls` row with a non-null
   stance; re-running same day → still one row; cache-hit path writes nothing.
2. Old cached reports (no `stance` key) still validate and render.
3. `/ledger` and `stocks ledger` render with an empty DB (all-zero summary, no
   division-by-zero) and with partially matured calls.
4. `evaluate_pending` is idempotent and makes zero network calls when nothing
   is pending; a yfinance outage degrades to "pending", never a 500.
5. No new LLM call sites anywhere in the diff (`grep` for new Agent/model
   usage); all ledger numbers are code-computed.
6. `uv run pytest` green; new tests cover §7.
