import { createFileRoute } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { optimizePortfolio } from '../server/portfolio'
import type { PortfolioMetrics } from '../server/portfolio'
import { useWatchlist } from '../lib/watchlist'

export const Route = createFileRoute('/portfolio')({ component: Portfolio })

type Objective = 'max_sharpe' | 'min_risk'

interface Row {
  symbol: string
  value: string // dollar position, optional; drives the "current" comparison
}

const pct = (n: number) => `${(n * 100).toFixed(1)}%`

function MetricsCard({ title, m }: { title: string; m: PortfolioMetrics }) {
  const rows = Object.entries(m.weights)
    .filter(([, w]) => w > 0.0005)
    .sort((a, b) => b[1] - a[1])
  return (
    <div className="rounded-2xl border border-[var(--line)] p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      <dl className="mb-4 grid grid-cols-3 gap-2 text-sm">
        <div>
          <dt className="text-xs text-[var(--sea-ink-soft)]">Exp. return</dt>
          <dd className="font-semibold">{pct(m.expected_return)}</dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--sea-ink-soft)]">Volatility</dt>
          <dd className="font-semibold">{pct(m.volatility)}</dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--sea-ink-soft)]">Sharpe</dt>
          <dd className="font-semibold">{m.sharpe.toFixed(2)}</dd>
        </div>
      </dl>
      <ul className="space-y-1.5">
        {rows.map(([sym, w]) => (
          <li key={sym} className="text-sm">
            <div className="flex justify-between">
              <span className="font-medium">{sym}</span>
              <span className="text-[var(--sea-ink-soft)]">{pct(w)}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--chip-bg)]">
              <div
                className="h-full rounded-full bg-[linear-gradient(90deg,#56c6be,#7ed3bf)]"
                style={{ width: `${Math.min(100, w * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Portfolio() {
  const { items, setValue } = useWatchlist()
  const [rows, setRows] = useState<Row[]>([])
  const [objective, setObjective] = useState<Objective>('max_sharpe')

  // Seed rows from the watchlist (symbol + saved position) once it loads.
  useEffect(() => {
    setRows((cur) => {
      if (cur.length > 0) return cur
      if (items.length === 0) return [{ symbol: '', value: '' }]
      return items.map((i) => ({ symbol: i.symbol, value: i.value != null ? String(i.value) : '' }))
    })
  }, [items])

  const mutation = useMutation({
    mutationFn: (vars: { holdings: { symbol: string; value?: number }[]; objective: Objective }) =>
      optimizePortfolio({ data: vars }),
  })

  const holdings = useMemo(
    () =>
      rows
        .map((r) => ({
          symbol: r.symbol.trim().toUpperCase(),
          value: r.value ? Number(r.value) : undefined,
        }))
        .filter((h) => h.symbol),
    [rows],
  )

  const update = (i: number, patch: Partial<Row>) =>
    setRows((cur) => cur.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  const addRow = () => setRows((cur) => [...cur, { symbol: '', value: '' }])
  const removeRow = (i: number) => setRows((cur) => cur.filter((_, idx) => idx !== i))

  const run = () => {
    // Persist entered positions back to watchlist names so they prefill next time.
    for (const r of rows) {
      const sym = r.symbol.trim().toUpperCase()
      if (sym) setValue(sym, r.value ? Number(r.value) : undefined)
    }
    mutation.mutate({ holdings, objective })
  }

  const resp = mutation.data

  return (
    <main className="page-wrap px-4 pb-8 pt-10">
      <h1 className="mb-1 text-3xl font-bold tracking-tight">Portfolio</h1>
      <p className="mb-6 text-sm text-[var(--sea-ink-soft)]">
        Mean-variance allocation over historical returns — research context, not advice. Optional
        dollar positions enable a current-vs-optimal comparison.
      </p>

      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={r.symbol}
              onChange={(e) => update(i, { symbol: e.target.value })}
              placeholder="Ticker"
              className="w-32 rounded-xl border border-[var(--line)] bg-transparent px-3 py-2 text-sm uppercase"
            />
            <input
              value={r.value}
              onChange={(e) => update(i, { value: e.target.value })}
              placeholder="$ position (optional)"
              inputMode="decimal"
              className="w-44 rounded-xl border border-[var(--line)] bg-transparent px-3 py-2 text-sm"
            />
            <button
              onClick={() => removeRow(i)}
              className="rounded-full border border-[var(--line)] px-3 py-1 text-xs"
            >
              Remove
            </button>
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button onClick={addRow} className="rounded-full border border-[var(--line)] px-3 py-1 text-xs">
          + Add ticker
        </button>
        <button
          onClick={() => setObjective((o) => (o === 'max_sharpe' ? 'min_risk' : 'max_sharpe'))}
          className="rounded-full border border-[var(--line)] px-3 py-1 text-xs"
          title="max_sharpe = highest risk-adjusted return; min_risk = lowest volatility"
        >
          objective: {objective}
        </button>
        <button
          onClick={run}
          disabled={mutation.isPending || holdings.length === 0}
          className="rounded-full border border-[var(--line)] bg-[var(--chip-bg)] px-4 py-1.5 text-sm font-semibold disabled:opacity-50"
        >
          {mutation.isPending ? 'Optimizing…' : 'Optimize'}
        </button>
      </div>

      {resp && resp.available === false && (
        <p className="mt-6 rounded-xl border border-amber-400/40 bg-amber-500/10 p-4 text-sm">
          {resp.reason}
        </p>
      )}

      {mutation.isError && (
        <p className="mt-6 rounded-xl border border-red-400/40 bg-red-500/10 p-4 text-sm">
          {mutation.error instanceof Error ? mutation.error.message : 'Optimization failed.'}
        </p>
      )}

      {resp && resp.available && (
        <div className="mt-8">
          {resp.result.warnings.length > 0 && (
            <ul className="mb-4 space-y-1 text-xs text-amber-500">
              {resp.result.warnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          )}
          <div className="grid gap-6 sm:grid-cols-2">
            <MetricsCard title={`Optimal (${resp.result.objective})`} m={resp.result.optimal} />
            {resp.result.current && <MetricsCard title="Current allocation" m={resp.result.current} />}
          </div>
          {resp.result.efficient_frontier.length > 0 && (
            <section className="mt-6">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--sea-ink-soft)]">
                Efficient frontier
              </h2>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-[var(--sea-ink-soft)]">
                {resp.result.efficient_frontier.map((p, i) => (
                  <span key={i}>
                    σ {pct(p.volatility)} → r {pct(p.expected_return)}
                  </span>
                ))}
              </div>
            </section>
          )}
          <p className="mt-6 text-xs text-[var(--sea-ink-soft)]">{resp.result.disclaimer}</p>
        </div>
      )}
    </main>
  )
}
