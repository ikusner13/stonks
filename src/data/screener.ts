import YahooFinance from "yahoo-finance2"

export type ScreenId =
  | "aggressive_small_caps"
  | "conservative_foreign_funds"
  | "day_gainers"
  | "day_losers"
  | "growth_technology_stocks"
  | "high_yield_bond"
  | "most_actives"
  | "most_shorted_stocks"
  | "portfolio_anchors"
  | "small_cap_gainers"
  | "solid_large_growth_funds"
  | "solid_midcap_growth_funds"
  | "top_mutual_funds"
  | "undervalued_growth_stocks"
  | "undervalued_large_caps"

export const SCREEN_IDS: ScreenId[] = [
  "aggressive_small_caps",
  "conservative_foreign_funds",
  "day_gainers",
  "day_losers",
  "growth_technology_stocks",
  "high_yield_bond",
  "most_actives",
  "most_shorted_stocks",
  "portfolio_anchors",
  "small_cap_gainers",
  "solid_large_growth_funds",
  "solid_midcap_growth_funds",
  "top_mutual_funds",
  "undervalued_growth_stocks",
  "undervalued_large_caps",
]

export interface ScreenedQuote {
  symbol: string
  name: string
  marketCap?: number
  peRatio?: number
  forwardPe?: number
  price?: number
  changePercent?: number
}

const yf = new YahooFinance({ suppressNotices: ["yahooSurvey"] })

export async function runScreen(scrId: ScreenId, count = 25): Promise<ScreenedQuote[]> {
  try {
    const res = await yf.screener({ scrIds: scrId, count })
    return res.quotes.map((q) => {
      const out: ScreenedQuote = {
        symbol: q.symbol,
        name: q.shortName ?? q.longName ?? q.symbol,
      }
      if (q.marketCap != null) out.marketCap = q.marketCap
      if (q.trailingPE != null) out.peRatio = q.trailingPE
      if (q.forwardPE != null) out.forwardPe = q.forwardPE
      if (q.regularMarketPrice != null) out.price = q.regularMarketPrice
      if (q.regularMarketChangePercent != null) out.changePercent = q.regularMarketChangePercent
      return out
    })
  } catch {
    return []
  }
}
