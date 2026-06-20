import { useCallback, useEffect, useState } from 'react'

const KEY = 'watchlist'

function read(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

function write(symbols: string[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(symbols))
  } catch {
    // ignore storage failures (private mode, quota)
  }
}

// Client-side watchlist persisted to localStorage. Starts empty on first render
// (server + hydration) then loads, matching the app's no-flash theme approach.
export function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>([])

  useEffect(() => {
    setSymbols(read())
  }, [])

  const add = useCallback((sym: string) => {
    const s = sym.toUpperCase()
    setSymbols((cur) => {
      if (cur.includes(s)) return cur
      const next = [...cur, s]
      write(next)
      return next
    })
  }, [])

  const remove = useCallback((sym: string) => {
    const s = sym.toUpperCase()
    setSymbols((cur) => {
      const next = cur.filter((x) => x !== s)
      write(next)
      return next
    })
  }, [])

  const has = useCallback((sym: string) => symbols.includes(sym.toUpperCase()), [symbols])

  return { symbols, add, remove, has }
}
