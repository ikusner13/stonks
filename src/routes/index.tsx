import { createFileRoute, Link } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { discover } from '../server/discovery'
import { useWatchlist } from '../lib/watchlist'

export const Route = createFileRoute('/')({ component: Discover })

const EXAMPLES = [
  'AI infrastructure under $100B market cap',
  'Undervalued large caps',
  'Growth technology stocks',
  'Most active stocks today',
]

function fmtCap(n?: number): string {
  if (n == null) return 'n/a'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function Discover() {
  const [goal, setGoal] = useState('')
  const { add, has } = useWatchlist()
  const mutation = useMutation({ mutationFn: (g: string) => discover({ data: g }) })

  function submit(g: string) {
    const trimmed = g.trim()
    if (trimmed) {
      setGoal(trimmed)
      mutation.mutate(trimmed)
    }
  }

  return (
    <main className="page-wrap px-4 pb-8 pt-10">
      <h1 className="mb-2 text-3xl font-bold tracking-tight">Discover ideas</h1>
      <p className="mb-6 text-sm text-[var(--sea-ink-soft)]">
        Describe what you're looking for. Candidates are validated against real market data —
        hallucinated tickers are dropped and numeric filters enforced in code.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit(goal)
        }}
        className="flex gap-2"
      >
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. AI infrastructure under $100B market cap"
          className="flex-1 rounded-xl border border-[var(--line)] bg-transparent px-4 py-2.5 text-sm outline-none focus:border-[var(--sea-ink-soft)]"
        />
        <button
          type="submit"
          disabled={mutation.isPending || !goal.trim()}
          className="rounded-xl border border-[var(--line)] bg-[var(--chip-bg)] px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
        >
          {mutation.isPending ? 'Researching…' : 'Discover'}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => submit(ex)}
            disabled={mutation.isPending}
            className="rounded-full border border-[var(--line)] px-3 py-1 text-xs text-[var(--sea-ink-soft)] disabled:opacity-50"
          >
            {ex}
          </button>
        ))}
      </div>

      {mutation.isPending && (
        <p className="mt-8 text-sm text-[var(--sea-ink-soft)]">
          Interpreting the goal, screening, validating candidates against live data… this takes a
          few seconds.
        </p>
      )}

      {mutation.isError && (
        <p className="mt-8 rounded-xl border border-red-400/40 bg-red-500/10 p-4 text-sm">
          {mutation.error instanceof Error ? mutation.error.message : 'Discovery failed.'}
        </p>
      )}

      {mutation.data && (
        <section className="mt-8">
          <p className="mb-4 text-sm text-[var(--sea-ink-soft)]">
            Interpreted as: {mutation.data.interpretation}
          </p>
          {mutation.data.candidates.length === 0 ? (
            <p className="text-sm">No candidates survived validation and filtering.</p>
          ) : (
            <ul className="space-y-3">
              {mutation.data.candidates.map((c) => (
                <li
                  key={c.symbol}
                  className="rounded-2xl border border-[var(--line)] p-4"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <Link
                      to="/research/$symbol"
                      params={{ symbol: c.symbol }}
                      className="text-lg font-semibold no-underline"
                    >
                      {c.symbol} <span className="text-sm font-normal text-[var(--sea-ink-soft)]">{c.name}</span>
                    </Link>
                    <div className="flex items-center gap-3 text-xs text-[var(--sea-ink-soft)]">
                      <span>cap {fmtCap(c.marketCap)}</span>
                      <span>P/E {c.peRatio?.toFixed(2) ?? 'n/a'}</span>
                      <span className="rounded-full border border-[var(--line)] px-2 py-0.5">{c.source}</span>
                      <button
                        onClick={() => add(c.symbol)}
                        disabled={has(c.symbol)}
                        className="rounded-full border border-[var(--line)] px-2 py-0.5 disabled:opacity-50"
                      >
                        {has(c.symbol) ? 'Saved' : '+ Watchlist'}
                      </button>
                    </div>
                  </div>
                  <p className="mt-2 text-sm text-[var(--sea-ink-soft)]">{c.rationale}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  )
}
