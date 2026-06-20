import { createFileRoute, Link } from '@tanstack/react-router'
import { useWatchlist } from '../lib/watchlist'

export const Route = createFileRoute('/watchlist')({ component: Watchlist })

function Watchlist() {
  const { symbols, remove } = useWatchlist()

  return (
    <main className="page-wrap px-4 pb-8 pt-10">
      <h1 className="mb-6 text-3xl font-bold tracking-tight">Watchlist</h1>

      {symbols.length === 0 ? (
        <p className="text-sm text-[var(--sea-ink-soft)]">
          Nothing saved yet. Add tickers from{' '}
          <Link to="/" className="underline">
            Discover
          </Link>{' '}
          or a research page.
        </p>
      ) : (
        <ul className="space-y-2">
          {symbols.map((sym) => (
            <li
              key={sym}
              className="flex items-center justify-between rounded-xl border border-[var(--line)] px-4 py-3"
            >
              <Link
                to="/research/$symbol"
                params={{ symbol: sym }}
                className="font-semibold no-underline"
              >
                {sym}
              </Link>
              <button
                onClick={() => remove(sym)}
                className="rounded-full border border-[var(--line)] px-3 py-1 text-xs"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}
