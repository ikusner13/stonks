import { withCache } from "../lib/cache.js"
import { fetchFinnhubNews, fetchFinnhubQuote } from "./finnhub.js"
import { fetchYahooFundamentals, fetchYahooNews, fetchYahooQuote } from "./yahoo.js"

// Intraday TTL: repeated research/discovery within the window reuse one fetch.
const DATA_TTL_MS = 15 * 60_000

export interface TickerData {
  symbol: string
  fetchedAt: string
  quote: { price: number; currency: string; change: number; changePercent: number } | null
  fundamentals: {
    marketCap?: number
    peRatio?: number
    forwardPe?: number
    profitMargin?: number
    revenue?: number
    [k: string]: number | undefined
  }
  news: { title: string; url: string; publishedAt: string; source: string }[]
}

async function safe<T>(fn: () => Promise<T>, fallback: T): Promise<T> {
  try {
    return await fn()
  } catch {
    return fallback
  }
}

export async function fetchTickerData(
  symbol: string,
  opts?: { fresh?: boolean },
): Promise<TickerData> {
  const { value } = await withCache(
    "data",
    symbol.toUpperCase(),
    DATA_TTL_MS,
    () => fetchTickerDataUncached(symbol),
    { fresh: opts?.fresh },
  )
  return value
}

async function fetchTickerDataUncached(symbol: string): Promise<TickerData> {
  const useFinnhub = Boolean(process.env.FINNHUB_API_KEY)

  const [yahooQuote, fundamentals, yahooNews, finnhubQuote, finnhubNews] = await Promise.all([
    safe(() => fetchYahooQuote(symbol), null),
    safe(() => fetchYahooFundamentals(symbol), {} as TickerData["fundamentals"]),
    safe(() => fetchYahooNews(symbol), [] as TickerData["news"]),
    useFinnhub ? safe(() => fetchFinnhubQuote(symbol), null) : Promise.resolve(null),
    useFinnhub ? safe(() => fetchFinnhubNews(symbol), [] as TickerData["news"]) : Promise.resolve([] as TickerData["news"]),
  ])

  // Prefer Finnhub's live quote when available, else fall back to Yahoo.
  const quote = finnhubQuote ?? yahooQuote

  // Merge news, dedupe by URL.
  const seen = new Set<string>()
  const news = [...finnhubNews, ...yahooNews].filter((n) => {
    if (seen.has(n.url)) return false
    seen.add(n.url)
    return true
  })

  return {
    symbol,
    fetchedAt: new Date().toISOString(),
    quote,
    fundamentals,
    news,
  }
}
