import YahooFinance from "yahoo-finance2"

const yf = new YahooFinance()

export interface YahooQuote {
  price: number
  currency: string
  change: number
  changePercent: number
}

export interface YahooFundamentals {
  marketCap?: number
  peRatio?: number
  forwardPe?: number
  profitMargin?: number
  revenue?: number
  [k: string]: number | undefined
}

export interface YahooNewsItem {
  title: string
  url: string
  publishedAt: string
  source: string
}

export async function fetchYahooQuote(symbol: string): Promise<YahooQuote | null> {
  const q = await yf.quote(symbol)
  if (q.regularMarketPrice == null) return null
  return {
    price: q.regularMarketPrice,
    currency: q.currency ?? "USD",
    change: q.regularMarketChange ?? 0,
    changePercent: q.regularMarketChangePercent ?? 0,
  }
}

export async function fetchYahooFundamentals(symbol: string): Promise<YahooFundamentals> {
  const s = await yf.quoteSummary(symbol, {
    modules: ["summaryDetail", "defaultKeyStatistics", "financialData"],
  })
  const out: YahooFundamentals = {}
  const marketCap = s.summaryDetail?.marketCap
  const peRatio = s.summaryDetail?.trailingPE
  const forwardPe = s.summaryDetail?.forwardPE ?? s.defaultKeyStatistics?.forwardPE
  const profitMargin = s.financialData?.profitMargins ?? s.defaultKeyStatistics?.profitMargins
  const revenue = s.financialData?.totalRevenue

  if (marketCap != null) out.marketCap = marketCap
  if (peRatio != null) out.peRatio = peRatio
  if (forwardPe != null) out.forwardPe = forwardPe
  if (profitMargin != null) out.profitMargin = profitMargin
  if (revenue != null) out.revenue = revenue
  return out
}

export async function fetchYahooNews(symbol: string, count = 10): Promise<YahooNewsItem[]> {
  const res = await yf.search(symbol, { newsCount: count, quotesCount: 0 })
  return res.news.map((n) => ({
    title: n.title,
    url: n.link,
    publishedAt: n.providerPublishTime.toISOString(),
    source: n.publisher,
  }))
}
