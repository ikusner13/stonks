import { createFileRoute } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { research } from '../server/research'
import { useWatchlist } from '../lib/watchlist'

export const Route = createFileRoute('/research/$symbol')({ component: Research })

type Mode = 'thorough' | 'cheap'

function Badge({ level }: { level: 'low' | 'medium' | 'high' }) {
  const cls =
    level === 'high'
      ? 'border-emerald-400/50 bg-emerald-500/10'
      : level === 'medium'
        ? 'border-amber-400/50 bg-amber-500/10'
        : 'border-red-400/50 bg-red-500/10'
  return <span className={`rounded-full border px-2 py-0.5 text-xs ${cls}`}>{level}</span>
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--sea-ink-soft)]">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Research() {
  const { symbol } = Route.useParams()
  const sym = symbol.toUpperCase()
  const { add, remove, has } = useWatchlist()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<Mode>('thorough')

  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['research', sym, mode],
    queryFn: () => research({ data: { symbol: sym, mode } }),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const refresh = () => {
    // Deliberate bypass of the persistent server-side report cache (W3).
    queryClient.fetchQuery({
      queryKey: ['research', sym, mode],
      queryFn: () => research({ data: { symbol: sym, mode, fresh: true } }),
    })
  }

  if (isPending) {
    return (
      <main className="page-wrap px-4 pb-8 pt-10">
        <h1 className="text-2xl font-bold">{sym}</h1>
        <p className="mt-4 text-sm text-[var(--sea-ink-soft)]">
          Fetching market data, then running research + critic review. This takes several seconds…
        </p>
      </main>
    )
  }

  if (isError) {
    return (
      <main className="page-wrap px-4 pb-8 pt-10">
        <h1 className="text-2xl font-bold">{sym}</h1>
        <p className="mt-4 rounded-xl border border-red-400/40 bg-red-500/10 p-4 text-sm">
          {error instanceof Error ? error.message : 'Research failed.'}
        </p>
      </main>
    )
  }

  const { ticker, report, critique, revised } = data
  const q = ticker.quote

  return (
    <main className="page-wrap px-4 pb-8 pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-2xl font-bold">
          {report.companyName} <span className="text-[var(--sea-ink-soft)]">({report.symbol})</span>
        </h1>
        <div className="flex items-center gap-3 text-sm">
          {q && (
            <span>
              {q.price.toLocaleString()} {q.currency}{' '}
              <span className={q.change >= 0 ? 'text-emerald-500' : 'text-red-500'}>
                {q.change >= 0 ? '+' : ''}
                {q.changePercent.toFixed(2)}%
              </span>
            </span>
          )}
          <span className="text-[var(--sea-ink-soft)]">confidence: {report.confidence}</span>
          <button
            onClick={() => setMode((m) => (m === 'thorough' ? 'cheap' : 'thorough'))}
            className="rounded-full border border-[var(--line)] px-3 py-1 text-xs"
            title="thorough = premium critic chain; cheap = workhorse critic, no revision"
          >
            mode: {mode}
          </button>
          <button
            onClick={refresh}
            disabled={isFetching}
            className="rounded-full border border-[var(--line)] px-3 py-1 text-xs disabled:opacity-50"
          >
            {isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            onClick={() => (has(sym) ? remove(sym) : add(sym))}
            className="rounded-full border border-[var(--line)] px-3 py-1 text-xs"
          >
            {has(sym) ? 'Remove' : '+ Watchlist'}
          </button>
        </div>
      </div>

      <Section title="Summary">
        <p className="text-sm">{report.summary}</p>
      </Section>

      <Section title="Key metrics">
        <ul className="space-y-2">
          {report.keyMetrics.map((m, i) => (
            <li key={i} className="text-sm">
              <span className="font-semibold">{m.label}: {m.value}</span>
              <span className="block text-[var(--sea-ink-soft)]">{m.interpretation}</span>
            </li>
          ))}
        </ul>
      </Section>

      <div className="grid gap-6 sm:grid-cols-2">
        <Section title="Bull case">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {report.thesis.bull.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </Section>
        <Section title="Bear case">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {report.thesis.bear.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </Section>
      </div>

      <Section title="Valuation context">
        <p className="text-sm">{report.valuationContext}</p>
      </Section>

      <Section title="Risks">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {report.risks.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      </Section>

      <Section title="Things to investigate">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {report.thingsToInvestigate.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      </Section>

      {ticker.news.length > 0 && (
        <Section title="Recent news">
          <ul className="space-y-1 text-sm">
            {ticker.news.map((n, i) => (
              <li key={i}>
                <a href={n.url} target="_blank" rel="noreferrer" className="no-underline hover:underline">
                  {n.title}
                </a>{' '}
                <span className="text-xs text-[var(--sea-ink-soft)]">— {n.source}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={`Critic review${revised ? ' · report was revised' : ''}`}>
        <div className="rounded-2xl border border-[var(--line)] p-4 text-sm">
          <p className="mb-2">
            Fabrication check:{' '}
            <span className={critique.fabricationCheck.passed ? 'text-emerald-500' : 'text-red-500'}>
              {critique.fabricationCheck.passed ? 'PASSED' : 'FAILED'}
            </span>{' '}
            <span className="text-[var(--sea-ink-soft)]">— {critique.fabricationCheck.details}</span>
          </p>
          <p className="mb-2 text-[var(--sea-ink-soft)]">
            Suggested confidence: {critique.suggestedConfidence}
          </p>
          {critique.issues.length > 0 ? (
            <ul className="space-y-2">
              {critique.issues.map((iss, i) => (
                <li key={i}>
                  <Badge level={iss.severity} /> <span className="font-medium">{iss.field}</span>
                  <span className="block text-[var(--sea-ink-soft)]">{iss.problem}</span>
                  <span className="block text-[var(--sea-ink-soft)]">fix: {iss.fix}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[var(--sea-ink-soft)]">No issues raised.</p>
          )}
          <p className="mt-3 text-[var(--sea-ink-soft)]">{critique.overallAssessment}</p>
        </div>
      </Section>
    </main>
  )
}
