const BASE = "https://finnhub.io/api/v1"

export interface FinnhubQuote {
  price: number
  currency: string
  change: number
  changePercent: number
}

export interface FinnhubNewsItem {
  title: string
  url: string
  publishedAt: string
  source: string
}

interface QuoteResponse {
  c?: number // current
  d?: number // change
  dp?: number // percent change
}

interface NewsResponse {
  headline?: string
  url?: string
  datetime?: number // unix seconds
  source?: string
}

function key(): string | undefined {
  return process.env.FINNHUB_API_KEY
}

export async function fetchFinnhubQuote(symbol: string): Promise<FinnhubQuote | null> {
  const token = key()
  if (!token) return null
  const res = await fetch(`${BASE}/quote?symbol=${encodeURIComponent(symbol)}&token=${token}`)
  if (!res.ok) return null
  const data = (await res.json()) as QuoteResponse
  if (data.c == null || data.c === 0) return null
  return {
    price: data.c,
    currency: "USD",
    change: data.d ?? 0,
    changePercent: data.dp ?? 0,
  }
}

export async function fetchFinnhubNews(symbol: string): Promise<FinnhubNewsItem[]> {
  const token = key()
  if (!token) return []
  const to = new Date()
  const from = new Date(to.getTime() - 14 * 24 * 60 * 60 * 1000)
  const fmt = (d: Date): string => d.toISOString().slice(0, 10)
  const url = `${BASE}/company-news?symbol=${encodeURIComponent(symbol)}&from=${fmt(from)}&to=${fmt(to)}&token=${token}`
  const res = await fetch(url)
  if (!res.ok) return []
  const data = (await res.json()) as NewsResponse[]
  if (!Array.isArray(data)) return []
  return data
    .filter((n) => n.headline && n.url)
    .map((n) => ({
      title: n.headline as string,
      url: n.url as string,
      publishedAt: n.datetime ? new Date(n.datetime * 1000).toISOString() : new Date().toISOString(),
      source: n.source ?? "Finnhub",
    }))
}
