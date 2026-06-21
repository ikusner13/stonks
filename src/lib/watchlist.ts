import { useCallback, useEffect, useState } from 'react'

const KEY = 'watchlist'

export interface WatchItem {
  symbol: string
  value?: number // optional dollar position, used by /portfolio
}

// Accepts the legacy `string[]` shape and the current `WatchItem[]` shape so
// existing saved watchlists keep working after the positions upgrade.
function parse(raw: string | null): WatchItem[] {
  if (!raw) return []
  try {
    const data = JSON.parse(raw) as unknown
    if (!Array.isArray(data)) return []
    return data
      .map((entry): WatchItem | null => {
        if (typeof entry === 'string') return { symbol: entry }
        if (entry && typeof entry === 'object' && 'symbol' in entry) {
          const e = entry as { symbol: unknown; value: unknown }
          if (typeof e.symbol !== 'string') return null
          return { symbol: e.symbol, value: typeof e.value === 'number' ? e.value : undefined }
        }
        return null
      })
      .filter((x): x is WatchItem => x !== null)
  } catch {
    return []
  }
}

function read(): WatchItem[] {
  if (typeof window === 'undefined') return []
  return parse(window.localStorage.getItem(KEY))
}

function write(items: WatchItem[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    // ignore storage failures (private mode, quota)
  }
}

// Client-side watchlist persisted to localStorage. Starts empty on first render
// (server + hydration) then loads, matching the app's no-flash theme approach.
export function useWatchlist() {
  const [items, setItems] = useState<WatchItem[]>([])

  useEffect(() => {
    setItems(read())
  }, [])

  const add = useCallback((sym: string) => {
    const s = sym.toUpperCase()
    setItems((cur) => {
      if (cur.some((i) => i.symbol === s)) return cur
      const next = [...cur, { symbol: s }]
      write(next)
      return next
    })
  }, [])

  const remove = useCallback((sym: string) => {
    const s = sym.toUpperCase()
    setItems((cur) => {
      const next = cur.filter((i) => i.symbol !== s)
      write(next)
      return next
    })
  }, [])

  // Set (or clear) the dollar position for a symbol already on the watchlist.
  const setValue = useCallback((sym: string, value: number | undefined) => {
    const s = sym.toUpperCase()
    setItems((cur) => {
      if (!cur.some((i) => i.symbol === s)) return cur
      const next = cur.map((i) => (i.symbol === s ? { ...i, value } : i))
      write(next)
      return next
    })
  }, [])

  const has = useCallback(
    (sym: string) => items.some((i) => i.symbol === sym.toUpperCase()),
    [items],
  )

  const symbols = items.map((i) => i.symbol)

  return { items, symbols, add, remove, has, setValue }
}
