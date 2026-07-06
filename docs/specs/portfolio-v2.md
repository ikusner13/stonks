# Portfolio v2 spec (transactions, alerts, visuals)

Scope: three workstreams on top of shipped go-live v1 (single-user, no auth).
Historical note: this spec predates the SnapTrade integration. Broker sync is
now in scope for the shipped app and is the source of truth for holdings, cash,
and transactions. JS charting libraries remain out of scope (repo idiom is
server-computed SVG/CSS), as does any LLM involvement in portfolio math.

Orchestration roles: Fable plans/judges, gpt-5.5 (codex exec, reasoning effort
**high**) codes, Opus reviews.

Global rules for every task:
- Obey repo CLAUDE.md: code computes, LLM narrates; never cache a failure.
- `uv run pytest -q` and `uv run ruff check` must pass before a task is done.
- Match existing style: Pydantic models, module-level functions, Jinja+HTMX
  partials, try/except → `_error_partial` pattern in routes, `db.connect()`
  for all SQLite.
- Every behavior change updates methodology.md / architecture.md /
  operations.md / README in the same task (per-task "docs" line).
- Zero new dependencies. httpx (webhook), stdlib math (XIRR, SVG geometry).

## Workstream DAG

```
W1 Transactions ledger + MWR      (T1 store/apply → T2 returns math → T3 UI)
W2 Daily job + alerts + what-if   (T4 scheduler+alerts → T5 what-if)
W3 Server-rendered visuals        (T6 donut+heatmap → T7 frontier+NAV)
W4 Docs/README sweep — after all merge
```

W1/W2/W3 are logically independent but ALL touch `portfolio.html` and
`app/web/app.py` → build each in an isolated worktree branched from main,
merge in order **W1 → W2 → W3**, rebasing each on the previous merge.

---

## W1 — Transactions ledger + money-weighted return

Design stance at implementation time: the lower-level ledger math is
deterministic, but the shipped web UI now mirrors broker state through
SnapTrade instead of accepting local state-entry forms. No lot tracking —
average-cost method, consistent with the single `avg_cost` field.

### T1. Store + apply semantics — new module `app/portfolio/transactions.py`

```python
SIDES = ("buy", "sell", "deposit", "withdraw")

class Transaction(BaseModel):
    id: int | None = None
    ts: str                    # ISO date "YYYY-MM-DD" (user-entered, no time)
    side: str                  # one of SIDES
    symbol: str | None         # required for buy/sell, None for deposit/withdraw
    shares: float | None       # required > 0 for buy/sell
    price: float | None        # required > 0 for buy/sell (per-share)
    amount: float              # buy/sell: shares*price rounded to cents; deposit/withdraw: user value > 0
    realized_pl: float | None  # sells only, set at apply time; None if avg_cost unknown
    note: str = ""

def init_transactions_db() -> None:
    # CREATE TABLE IF NOT EXISTS transactions (
    #   id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, side TEXT NOT NULL,
    #   symbol TEXT, shares REAL, price REAL, amount REAL NOT NULL,
    #   realized_pl REAL, note TEXT DEFAULT '',
    #   created_at TEXT DEFAULT CURRENT_TIMESTAMP)

def apply_transaction(txn: Transaction) -> Transaction:
    """Validate, mutate holdings/cash, insert row, return txn with id +
    realized_pl filled. All-or-nothing: validation errors raise ValueError
    BEFORE any write. Rules:

    - ts must parse as ISO date, not in the future (UTC today allowed).
    - buy:  cash_after = get_cash() - amount; < 0 → ValueError
            ("insufficient cash — record a deposit first or adjust cash").
            holdings: new_shares = old + shares;
            new_avg = (old_shares*old_avg + shares*price) / new_shares
            when old row exists AND old avg_cost is not None;
            no old row → avg = price; old avg_cost None → new avg None
            (unknown + known = unknown, never fabricate a basis).
    - sell: shares > held shares (1e-9 tolerance) → ValueError. holdings row
            shrinks; reaches ~0 shares (<1e-9) → delete row. avg_cost
            unchanged. realized_pl = (price - avg_cost) * shares rounded to
            cents, None if avg_cost is None. cash += amount.
    - deposit: cash += amount.  withdraw: cash -= amount, < 0 → ValueError.
    Symbol uppercased. Uses db.connect() + holdings.upsert/remove + get/set_cash."""

def delete_transaction(txn_id: int) -> None:
    """Delete the ROW ONLY — does NOT reverse holdings/cash effects (document
    in UI: 'removes the record, not its effect'). Reversal would need full
    replay; out of scope."""

def list_transactions(limit: int = 200, symbol: str | None = None) -> list[Transaction]
    # descending by ts, then id
```

### T2. Returns math — same module, pure functions

```python
def xirr(flows: list[tuple[str, float]]) -> float | None:
    """Annualized money-weighted return via bisection on NPV. flows =
    [(iso_date, amount)]; convention: deposits NEGATIVE (money in),
    withdrawals POSITIVE, terminal portfolio value POSITIVE (today).
    NPV(r) = sum(amt / (1+r)**(days_from_first/365.25)).
    Bisect r over (-0.9999, 10.0), 1e-7 tolerance, max 200 iters.
    Return None when: < 2 flows, all flows same sign, no sign change of NPV
    at the bounds, or span < 14 days (too short to annualize honestly)."""

class ReturnsSummary(BaseModel):
    mwr_annualized: float | None   # from xirr()
    mwr_note: str                  # why None, when None ("record deposits…")
    total_deposited: float
    total_withdrawn: float
    realized_pl_total: float
    realized_pl_by_year: dict[str, float]   # {"2026": 123.45}, sells only
    first_flow_date: str | None
    flow_count: int

def compute_returns(valuation: PortfolioValuation) -> ReturnsSummary:
    """MWR flows use ONLY deposit/withdraw txns (external flows) + terminal
    value valuation.total_with_cash dated UTC today. Buys/sells are internal
    (cash is inside the portfolio boundary) and MUST NOT appear as flows.
    No deposits/withdraws recorded → mwr None with explanatory note.
    valuation has unpriced symbols → mwr None ('portfolio not fully priced')."""
```

Docs: methodology.md new § "Transactions, realized P/L, and money-weighted
return" — exact avg-cost formula, sell P/L formula, XIRR flow convention and
bisection bounds, the internal-vs-external flow boundary.

### T3. UI + broker sync

- `portfolio.html` new section **G) Transactions** (after F): lazy
  `hx-get="/portfolio/transactions" hx-trigger="load, txns-changed from:body"`.
- `GET /portfolio/transactions` → `partials/transactions.html`: returns
  summary block (ReturnsSummary stats: MWR badge, deposited/withdrawn,
  realized P/L total + per-year mini-table) and the last 20 broker-imported
  transaction rows.
- Manual transaction state-entry, file import, and deletion controls were
  superseded by SnapTrade broker sync. Broker sync emits `txns-changed` and
  `holdings-changed`; the holdings table listens with
  `hx-trigger="holdings-changed from:body"` on a wrapping div that `hx-get`s
  `GET /portfolio/holdings`.
- MWR badge also added to NAV panel header (small, e.g. "MWR 12.3%/yr") —
  pass ReturnsSummary into nav partial context.

### T-W1 tests (`tests/test_transactions.py` + route additions)

- apply: buy math (fresh row, existing row weighted avg, None-avg stays None),
  insufficient cash raise (nothing written — assert cash AND holdings AND txn
  count unchanged), oversell raise, sell realized_pl + row deletion at zero,
  deposit/withdraw, future date raise, negative/zero amounts raise.
- xirr: known fixture (deposit -10000 on day 0, terminal 11000 at 1y →
  ~0.10 within 1e-4), two-flow same-sign → None, <14d span → None,
  loss case negative rate, multi-flow case vs hand-computed NPV sign checks.
- compute_returns: flows exclude buys/sells; per-year rollup; unpriced → None.
- broker sync route coverage owns web transaction refresh behavior.

---

## W2 — Daily job, drift alerts, what-if

### T4. In-process daily job + Discord webhook alert

No new services (deploy stays app+cloudflared). Asyncio task in FastAPI
lifespan. New module `app/jobs.py`:

```python
# app/config.py additions:
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")   # empty → alerts disabled
DAILY_JOB_HOUR_UTC = int(os.getenv("DAILY_JOB_HOUR_UTC", "21"))  # after US close
DRIFT_ALERT_ENABLED = os.getenv("DRIFT_ALERT_ENABLED", "1") == "1"

# app/jobs.py
def seconds_until_next(hour_utc: int, now: datetime) -> float   # pure, tested

async def run_daily_jobs() -> dict:
    """Returns {"snapshot": bool, "alert": str} for logging/tests.
    1. valuation = await value_holdings(); snapshots.record_snapshot(valuation)
       (fixes 'no page visit → no NAV point'; record_snapshot already skips
       partially-priced days).
    2. Drift alert, only when DRIFT_ALERT_ENABLED and DISCORD_WEBHOOK_URL set:
       plan = plan_rebalance(valuation, list_targets()); actionable =
       [i for i in plan.items if i.action != "hold"] if plan else [].
       Skip if empty. Dedupe: settings key 'last_drift_alert' holds
       'YYYY-MM-DD:<sorted actionable symbols csv>'; identical value for today
       OR same symbol set as the stored value → skip (one alert per distinct
       drift set; a NEW symbol crossing re-alerts even same day... simplify:
       skip when stored symbol set == current set, regardless of date;
       update key after send).
       Message (deterministic text, no LLM):
       'Rebalance drift: SYMB 27.1% vs target 20.0% → sell $1,234 (~4.56 sh)'
       one line per item + 'cash after: $X'. POST via httpx, 5s timeout,
       json={"content": msg}. Failure → log, DO NOT update dedupe key.

async def daily_loop() -> None:
    # while True: sleep seconds_until_next(...); try run_daily_jobs()
    # except Exception: logger.exception — the loop must never die.
```

- Wire: FastAPI lifespan (convert app to lifespan context if it isn't) —
  `task = asyncio.create_task(daily_loop())` on startup, cancel on shutdown.
  Env guard for tests: skip task when `DAILY_JOB_HOUR_UTC < 0`.
- Docs: operations.md env table (3 new vars, Discord webhook how-to link),
  architecture.md "background jobs" note + snapshot trigger update
  (page-visit OR daily job); methodology.md NAV snapshot rule update.

### T5. What-if: post-trade preview + contribution-only plan

```python
# plan.py: RebalanceItem gains one field
after_weight: float   # weight if plan executes: current + delta_usd/base_value (hold → current)

# new pure fn in plan.py
class ContributionItem(BaseModel):
    symbol: str
    price: float | None
    current_weight: float      # vs NEW base (base_value + contribution)
    target_weight: float
    buy_usd: float             # >= 0, cents
    buy_shares: float | None   # buy_usd/price 4dp, None if unpriced
    after_weight: float

class ContributionPlan(BaseModel):
    contribution: float
    base_after: float          # base_value + contribution
    leftover_cash: float       # contribution - sum(buy_usd) (>= 0)
    items: list[ContributionItem]   # buy_usd > 0 only, sorted desc

def plan_contribution(valuation, targets, contribution: float) -> ContributionPlan | None:
    """Buys-only rebalance of new money. None when contribution <= 0 or
    targets empty or base invalid (same guards as plan_rebalance).
    deficit_i = max(0, target_weight_i * base_after - current_value_i).
    total deficit <= contribution → buy_usd = deficit (leftover stays cash);
    else buy_usd = contribution * deficit_i / sum(deficits).
    Round cents; drop items < $1. No sells ever. Deterministic, no I/O."""
```

- Rebalance partial: add "after %" column from `after_weight`.
- UI: inside rebalance panel, small form `hx-post="/portfolio/whatif"`
  (amount input) → `partials/whatif.html` table (symbol, buy $, ~shares,
  now % → after %, leftover cash, `DS_DISCLAIMER` footer).

### T-W2 tests (`tests/test_jobs.py`, `tests/test_plan.py` additions)

- seconds_until_next: before/after/exactly-at hour, DST-irrelevant (UTC).
- run_daily_jobs: monkeypatched value_holdings + httpx post recorder —
  snapshot recorded; alert sent once, deduped on same symbol set, re-sent on
  changed set; webhook unset → no post; webhook post raises → dedupe key NOT
  updated; value_holdings raises → returns without alert, no crash.
- plan_contribution: fixed fixture — proportional split when short,
  full-deficit + leftover when flush, overweight symbol gets 0, unpriced
  symbol (buy_usd set, shares None), after_weight sums ≈ targets when fully
  funded, contribution <= 0 → None.
- after_weight on existing plan fixtures: hold → unchanged, buy/sell →
  target (within rounding).
- Route: POST /portfolio/whatif valid + invalid amount → error partial.

---

## W3 — Server-rendered visuals (no JS libs)

All geometry computed in Python (pattern: `snapshots._svg_polyline`), Jinja
renders. Colors: fixed colorblind-aware categorical palette (Okabe-Ito
extended to 12; slices beyond 11 → aggregate into gray "Other"); correlation
uses a blue↔white↔red diverging scale with the value ALWAYS printed in the
cell (color is redundant encoding). New module `app/web/charts.py` for pure
geometry+color helpers — unit-testable without templates.

### T6. Allocation donut + correlation heatmap

```python
# app/web/charts.py
PALETTE: list[str]  # 11 categorical hexes + OTHER_GRAY

class DonutSlice(BaseModel):
    label: str; value: float; pct: float; color: str
    path_d: str        # SVG arc path for a 220x220 viewBox, ring hole 0.62

def donut(slices: list[tuple[str, float]]) -> list[DonutSlice]
    """Sort desc, keep top 11, aggregate rest into 'Other'. Skip zero/negative
    values. Full-circle single slice → two half arcs (SVG can't arc 360°).
    Angles from 12 o'clock clockwise. Empty/all-zero input → []."""

def corr_color(rho: float) -> tuple[str, str]
    """(bg_hex, text_hex): linear interp white→#2166ac for rho<0,
    white→#b2182c for rho>0; text flips to white when |rho| > 0.6."""
```

- Donut into portfolio Health section (A/B): holdings by market value +
  a cash slice when cash > 0 — render inside `holdings_table` partial context
  or health partial (pick health; it already gets valuation). SVG + legend
  list (color chip, symbol, %, $). Unpriced symbols excluded, noted under.
- Heatmap: `CorrelationInsight` gains
  `matrix: dict[str, dict[str, float]] | None = None` — populate it in
  `analyze_correlation` from its existing matrix arg (round 2dp). NOTE
  correlation results are cached 24h (namespace `correlation`) — adding a
  field is backward compatible for pydantic parse of old cache entries
  (default None); heatmap renders only when matrix present ("refresh after
  cache expiry" note otherwise). Render in `portfolio_correlation.html`:
  symbols ordered by portfolio weight desc, `<table>` with inline
  `style="background:{{bg}};color:{{fg}}"` cells, diagonal blank, cells show
  ρ 2dp, pairs ≥ HIGH_CORRELATION get a bold border. Legend strip under.

### T7. Efficient-frontier SVG + NAV chart upgrade

```python
# charts.py
class ScatterChart(BaseModel):
    width: int = 600; height: int = 260
    frontier_polyline: str            # "x,y x,y ..." vol→x, return→y
    optimal_xy: tuple[float, float] | None
    current_xy: tuple[float, float] | None
    x_ticks: list[tuple[float, str]]  # (px, "12%") 3 ticks: min/mid/max
    y_ticks: list[tuple[float, str]]

def frontier_chart(frontier: list[FrontierPoint], optimal, current) -> ScatterChart | None
    # None when < 2 frontier points; 5% padding on both ranges;
    # current/optimal included in range computation so markers never clip.

def nav_area(points: list[NavSnapshot]) -> NavChart | None
    # extends _svg_polyline pattern: same polyline + closed fill path,
    # first/last date labels, min/max y labels, dotted baseline at first value.
```

- Frontier chart replaces the σ/r text list in `portfolio_results.html`:
  polyline + optimal marker (labeled dot) + current marker, axis tick labels.
  Keep the text list in a `<details>` fallback.
- NAV panel: swap bare polyline for the filled area variant, keep stat tiles
  and table. Move `_svg_polyline` logic into charts.py (`nav_area`), have
  snapshots.NavSeries keep `svg_polyline` field for compat OR migrate the
  field to the new chart model — migrate, update partial + tests together.
- Visual style: single accent color for line/fill (existing app accent from
  app.css/Tailwind slate/emerald scheme — match `portfolio_results` bars),
  fill at 15% opacity, 1.5px stroke, dark-bg friendly (page bg is Tailwind
  dark? match whatever base.html uses — check before styling).

### T-W3 tests (`tests/test_charts.py` + route/partial assertions)

- donut: pct sums ~100, arc angles sum to 360 (parse path or expose angles),
  top-11+Other aggregation, single-holding full circle, empty → [].
- corr_color: rho 0 → white bg, ±1 → deep ends, text flip beyond 0.6.
- frontier_chart: known 3-point fixture → coordinates hand-checked, padding,
  None on <2 points; markers within viewBox for extreme current point.
- nav_area: flat series no div-by-zero (existing guard), fill path closes.
- routes: health partial contains `<svg` + legend symbols; correlation
  partial with matrix fixture contains N² cells; optimizer partial contains
  frontier `<polyline`.

---

## W4 — Docs & README sweep (after W1–W3 merge)

One pass: README feature tour (transactions/MWR, alerts, what-if, charts),
methodology.md formulas match code exactly (avg-cost, XIRR convention +
bounds, contribution split, alert dedupe rule — hard-rule doc), architecture
module map + storage table (`transactions` table, `app/jobs.py`,
`app/web/charts.py`), operations.md env table (webhook vars, job hour).

## Acceptance (whole spec)

- `uv run pytest -q` green, `uv run ruff check` clean at every merge point.
- Manual smoke: portfolio page renders all panels with an empty DB (no
  transactions, no targets, no snapshots) — every new partial has a sane
  empty state, no 500s.
- No new pyproject dependencies; no JS added beyond existing HTMX/Tailwind
  CDN; no LLM calls introduced.
