# Go-live v1 spec (single-user)

Scope: harden + extend the app for personal live use. **Multi-user/auth is explicitly
deferred** — no user scoping anywhere in this spec. Deploy (docs/deployment.md) is an
ops task outside this spec.

Orchestration roles: Fable plans/judges, gpt-5.5 (codex exec, reasoning effort **high**
— override the config.toml `low` default with `-c model_reasoning_effort="high"`) codes.

Global rules for every task:
- Obey repo CLAUDE.md: code computes, LLM narrates; never cache a failure.
- `uv run pytest -q` and `uv run ruff check` must pass before a task is done.
- Match existing style: Pydantic models, module-level functions, Jinja+HTMX partials,
  try/except → `_error_partial` pattern in routes.
- Every behavior change updates docs in the same task (see per-task "docs" line).

## Task DAG

```
A (db consolidation + WAL + single-flight)   ──┐
B (LLM budget guard)                           │  independent of A
                                               ▼
C (cash tracking)  ──►  D (NAV snapshots)      D, E, F all touch portfolio.html —
                   ──►  E (targets+rebalance)  run D, E, F SEQUENTIALLY (or in
                        F (broker holdings)    isolated worktrees, merge in order D→E→F)
G (docs+README sweep, final)  — after all
```

A and B can run in parallel. C after A. D/E/F after C. G last.

---

## Task A — SQLite hardening + cache single-flight

### A1. One shared SQLite connection helper

`app/db.py` and `app/portfolio/holdings.py` each define an identical `_conn()`.
Replace with one shared helper in `app/db.py`:

```python
# app/db.py
from . import config  # module import, NOT `from .config import DB_PATH`

@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)   # read at call time → monkeypatchable
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
```

- `app/db.py` internals and `app/portfolio/holdings.py` both use `connect()`;
  delete `holdings._conn`. Reading `config.DB_PATH` lazily (via module attribute)
  is load-bearing: tests monkeypatch `app.config.DB_PATH` to a tmp file.
- New tables in later tasks also use `connect()`.

### A2. Single-flight in `app/cache.py::with_cache`

Two concurrent requests for the same uncached key currently both run `produce()`
(duplicate LLM spend / duplicate yfinance downloads). Coalesce in-process:

```python
_inflight: dict[str, tuple[asyncio.Lock, int]] = {}  # "ns:key" -> (lock, refcount)

async def with_cache(namespace, key, ttl_ms, produce, *, fresh=False):
    if not fresh:
        cached = read_cache(namespace, key)
        if cached is not None:
            return cached, True
    k = f"{namespace}:{key}"
    lock, n = _inflight.get(k, (asyncio.Lock(), 0))
    _inflight[k] = (lock, n + 1)
    try:
        async with lock:
            if not fresh:                      # re-check: loser of the race hits cache
                cached = read_cache(namespace, key)
                if cached is not None:
                    return cached, True
            value = await produce()
            if value is not None:
                write_cache(namespace, key, value, ttl_ms)
            return value, False
    finally:
        lock2, n2 = _inflight[k]
        if n2 <= 1:
            _inflight.pop(k, None)
        else:
            _inflight[k] = (lock2, n2 - 1)
```

- Single event loop → dict ops need no extra locking. `fresh=True` still bypasses the
  cache read but MUST still serialize through the lock (prevents a fresh + normal pair
  double-producing).
- Behavior contract unchanged otherwise: returns `(value, hit)`; exceptions from
  `produce()` propagate and cache nothing.

### A3. Tests (`tests/test_cache.py` additions, new `tests/test_db.py`)

- Two concurrent `with_cache` calls, same key, slow `produce` → `produce` called
  exactly once, both get the value, second reports `hit=True`.
- Different keys → both produce, no serialization (assert overlap via timestamps or
  a counter).
- `produce` raising → both callers see the exception, nothing cached, `_inflight`
  empty afterwards.
- `connect()` honors monkeypatched `app.config.DB_PATH`; `PRAGMA journal_mode`
  returns `wal`.
- Fill in `tests/test_holdings.py` (currently has ZERO test functions): upsert
  overwrites, remove is idempotent, `value_holdings` with `fetch_ticker_data`
  monkeypatched (priced, unpriced, empty portfolio; totals + weights math).

Docs: architecture.md caching section — note single-flight coalescing.

---

## Task B — Daily LLM budget guard

### B1. Config

`app/config.py`:

```python
DAILY_LLM_BUDGET_USD = float(os.getenv("DAILY_LLM_BUDGET_USD", "5"))  # 0 disables
```

### B2. New module `app/llm/budget.py`

```python
class BudgetExceededError(RuntimeError):
    def __init__(self, spent: float, limit: float): ...

def spent_today() -> float:
    """Sum totals.cost_usd of usage.jsonl events whose ts is today's UTC date."""
    # reuse usage.read_events(); filter e["ts"].startswith(date.today-in-UTC iso)

def check_budget() -> None:
    """Raise BudgetExceededError if DAILY_LLM_BUDGET_USD > 0 and
    spent_today() >= DAILY_LLM_BUDGET_USD. No-op when disabled (0)."""
```

- Check happens **before** a run starts; a run that crosses the limit mid-flight
  finishes (no mid-run abort). Resets naturally at UTC midnight.

### B3. Wire-in points (cache hits must NEVER be blocked)

- `app/llm/pipeline.py::research_ticker_cached` — call `check_budget()` at the top
  of the inner `produce()` (so a cached report costs $0 and always serves).
- `app/llm/discovery.py::discover_ideas` — `check_budget()` at function top (always
  costs money).
- `app/web/app.py` — in `research_report` and `discover` routes, catch
  `BudgetExceededError` → `_error_partial(request, f"Daily LLM budget reached
  (${e.spent:.2f} of ${e.limit:.2f}). Resets at midnight UTC; cached reports still
  load.")` (no retry_url).
- CLI is covered automatically via pipeline/discovery; no CLI changes.

### B4. Tests (new `tests/test_budget.py`)

- `spent_today` sums only today's events (write a tmp usage.jsonl via monkeypatched
  `USAGE_LOG`/`CACHE_DIR`; include a yesterday event and a malformed line).
- `check_budget` raises at/above limit, passes below, no-op at 0.
- Route test: monkeypatch pipeline to raise `BudgetExceededError` → response contains
  the budget message, status 200 (HTMX partial).

Docs: operations.md env table + a "cost controls" paragraph; README env-keys table row.

---

## Task C — Cash / dry-powder tracking

### C1. Settings KV + cash accessors (`app/db.py`)

```python
# in init_db():
# CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)

def get_setting(key: str) -> str | None: ...
def set_setting(key: str, value: str) -> None: ...

def get_cash() -> float:        # settings["cash"], default 0.0, tolerate bad value → 0.0
def set_cash(amount: float) -> None:   # reject negative → ValueError
```

### C2. Valuation surface (`app/portfolio/holdings.py`)

`PortfolioValuation` gains:

```python
cash: float = 0.0
total_with_cash: float = 0.0     # total_value + cash
cash_pct: float = 0.0            # cash / total_with_cash if > 0 else 0.0
```

`value_holdings()` reads `db.get_cash()` and fills these. **`total_value` and
per-holding `weight` semantics are unchanged** (securities-only) — health,
correlation, optimizer, performance all keep their current inputs. Only these
consumers change:

- `suggest_position_size` call sites (`app/web/app.py::research_report`): pass
  `valuation.total_with_cash` instead of `total_value` — new-position sizing is
  based on investable base including dry powder. (`decision_support.py` itself
  needs no change; it takes a number.)
- Task E's rebalance plan uses `total_with_cash` as its base.

### C3. UI

- `partials/holdings_table.html`: footer rows — Cash $X, Total (incl. cash) $Y,
  Dry powder Z%.
- Portfolio cash is now broker-mirrored by SnapTrade sync; there is no manual
  cash state-entry route or form.

### C4. Tests

- get/set cash round-trip, default 0, negative rejected.
- `value_holdings` cash math (cash-only portfolio: total_value 0, total_with_cash > 0,
  cash_pct 1.0).
- Broker sync route coverage owns cash refresh behavior.

Docs: methodology.md §position-sizing (base now includes cash), README feature tour.

---

## Task D — Daily NAV snapshots (real equity curve)

### D1. New module `app/portfolio/snapshots.py`

```python
class NavSnapshot(BaseModel):
    day: str            # UTC ISO date, PK
    total_value: float  # securities
    cash: float
    total_with_cash: float
    total_cost: float
    unrealized_pl: float

def init_snapshots_db() -> None:
    # CREATE TABLE IF NOT EXISTS nav_snapshots (
    #   day TEXT PRIMARY KEY, total_value REAL, cash REAL, total_with_cash REAL,
    #   total_cost REAL, unrealized_pl REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)

def record_snapshot(valuation: PortfolioValuation) -> bool:
    """INSERT OR REPLACE today's (UTC) row from the valuation. Last write of the
    day wins. SKIP (return False) when valuation.unpriced_symbols is non-empty or
    total_with_cash <= 0 — never persist a partially-priced NAV."""

def list_snapshots(limit: int = 365) -> list[NavSnapshot]:  # ascending by day
```

- Written opportunistically from `GET /portfolio` (`portfolio_page` calls
  `record_snapshot(valuation)` after `value_holdings()`). No scheduler — visiting
  the page is the trigger; the doc must say a day you never open the app has no point.
  Wrap in try/except + `logger.warning` — snapshot failure must never break the page.

### D2. Equity-curve panel

- Route `GET /portfolio/nav` → `partials/nav_history.html`, loaded via HTMX from
  portfolio.html (same lazy pattern as correlation/performance).
- Template context computed in Python (code computes — no JS charting):

```python
class NavSeries(BaseModel):
    points: list[NavSnapshot]
    change_1d: float | None      # vs previous snapshot ($)
    change_1d_pct: float | None
    change_total: float | None   # vs first snapshot ($)
    change_total_pct: float | None
    svg_polyline: str | None     # "x1,y1 x2,y2 ..." normalized to a 600x120 viewBox,
                                 # None when < 2 points
```

- Partial renders: sparkline `<svg><polyline points="{{...}}"/></svg>`, the deltas,
  and a compact table of the last 10 snapshots. `< 2` points → "Come back tomorrow —
  the curve starts after two daily snapshots."

### D3. Tests (new `tests/test_snapshots.py`)

- record: replace-same-day wins; skip on unpriced_symbols; skip on zero value.
- series math: change fields correct, svg_polyline None for <2 points, y range
  handles flat series (all equal values → no div-by-zero).

Docs: methodology.md new subsection "NAV history" (snapshot rule: last page view of
the UTC day, skip when partially priced — this is actual account history, unlike the
constant-weight backtest §5); architecture.md storage table.

---

## Task E — User target allocations + prescriptive rebalance plan

The existing optimizer-drift panel stays as-is. This adds *user-owned* targets and
deterministic suggested trades. New module `app/portfolio/plan.py`.

### E1. Targets store

```python
def init_targets_db() -> None:
    # CREATE TABLE IF NOT EXISTS targets (
    #   symbol TEXT PRIMARY KEY, target_weight REAL NOT NULL CHECK(target_weight >= 0))

class Target(BaseModel):
    symbol: str
    target_weight: float   # fraction 0-1

def list_targets() -> list[Target]
def set_targets(targets: list[Target]) -> None
    """Full replace (DELETE all + INSERT). Validate BEFORE writing:
    each weight in [0,1]; sum(weights) <= 1.0 + 1e-6, else ValueError.
    1 - sum = implicit cash target."""
def remove_target(symbol: str) -> None
```

### E2. Rebalance plan — pure function, the heart of the task

```python
class RebalanceItem(BaseModel):
    symbol: str
    price: float | None
    current_weight: float     # market_value / base (0 if not held)
    target_weight: float
    drift: float              # current - target
    action: str               # "buy" | "sell" | "hold"
    delta_usd: float          # signed; + = buy. 0 for hold.
    delta_shares: float | None  # delta_usd / price, 4dp; None if price is None

class RebalancePlan(BaseModel):
    base_value: float          # valuation.total_with_cash
    cash_now: float
    cash_after: float          # cash_now - sum(item.delta_usd)
    cash_target_weight: float  # 1 - sum(target weights)
    items: list[RebalanceItem] # sorted by |drift| desc
    untargeted: list[str]      # held symbols with NO targets row — excluded from trades
    threshold: float = DRIFT_THRESHOLD  # reuse decision_support.DRIFT_THRESHOLD

def plan_rebalance(valuation: PortfolioValuation, targets: list[Target]) -> RebalancePlan | None:
    """None when base_value <= 0 or targets empty. Rules:
    - universe = symbols WITH a targets row (union with holdings for current data).
      Held symbols without a row go to `untargeted` verbatim — no implied sell.
      An EXPLICIT 0-weight target does produce a sell.
    - current_weight uses market_value / base_value (recomputed here — do NOT reuse
      HoldingValuation.weight, whose denominator excludes cash).
    - action "hold" + delta 0 when |drift| <= threshold; otherwise delta_usd =
      (target - current) * base_value rounded to cents, delta_shares = delta_usd/price.
    - a targeted symbol that is unpriced (price None) and not held: delta_shares None,
      delta_usd still computed (user can figure shares at their broker).
    Deterministic, no I/O, no network."""
```

### E3. Routes + UI

- `GET /portfolio/targets` → `partials/targets_form.html`: editable rows
  (symbol, weight %) prefilled from `list_targets()`, plus current holdings without
  targets offered as blank rows; shows implicit cash %.
- `POST /portfolio/targets` — form arrays `symbol[]`, `weight_pct[]` (percent in UI,
  fractions in store). ValueError → error partial listing the problem ("weights sum
  to 112%"). Success → re-render targets form + trigger refresh of the plan panel
  (`HX-Trigger: targets-changed` response header; plan panel listens).
- `GET /portfolio/rebalance` → `partials/rebalance_plan.html`: the plan table
  (symbol, current %, target %, drift, action, $ amount, ~shares), cash now/after,
  untargeted list, `DS_DISCLAIMER` footer. Empty targets → prompt to set targets.
- "Adopt optimizer weights as targets" button inside the existing optimizer results
  partial: `POST /portfolio/targets/adopt` with `symbol[]`+`weight[]` hidden fields
  rendered from `result.optimal.weights` → calls `set_targets`, returns targets form.

### E4. Tests (new `tests/test_plan.py`)

- set_targets validation: sum > 1 rejects, negatives reject, replace semantics.
- plan math on a fixed valuation fixture: buy/sell/hold classification around the
  5% threshold, cash_after arithmetic, explicit-0 target sells, missing-row goes to
  untargeted, unpriced targeted symbol (delta_shares None), base includes cash.
- Route tests: POST valid/invalid targets; GET rebalance with and without targets.

Docs: methodology.md new §"Target allocations & rebalance plan" with the exact
formulas above; README feature tour bullet.

---

## Task F — Broker-mirrored holdings

This earlier holdings file-upload plan has been superseded. Holdings are mirrored
from SnapTrade broker sync, and the portfolio page exposes no local holdings
state-entry or file-upload controls.

Docs: README feature tour + operations.md (CSV format snippet).

---

## Task G — Docs & README sweep (final)

After A–F merge: one pass to verify README feature tour, methodology.md,
architecture.md (module map + storage/caching tables), operations.md (env table:
`DAILY_LLM_BUDGET_USD`) are consistent with the shipped behavior. methodology.md is
under the repo hard rule — every formula stated must match code exactly (rebalance
math, snapshot rule, sizing base incl. cash, budget check placement).

---

## Judge checklist (per task, Fable)

1. `uv run pytest -q` and `uv run ruff check` green — run them, don't trust claims.
2. Hard rules: no LLM-derived numbers; `with_cache` still never persists None/errors;
   budget check cannot block cache hits (trace the code path).
3. Contracts above implemented as specced — field names, skip rules, validation
   bounds, threshold reuse (`DRIFT_THRESHOLD` imported, not re-declared).
4. Weight-denominator discipline (the likeliest bug class): existing panels stay
   securities-only; sizing + rebalance use `total_with_cash`; `plan_rebalance`
   recomputes weights and does NOT reuse `HoldingValuation.weight`.
5. Routes follow the existing lenient-form + error-partial patterns; no stack traces
   to browser; snapshot write can't break `GET /portfolio`.
6. Docs updated in the same task, factually matching code.
