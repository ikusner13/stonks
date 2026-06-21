import { createServerFn } from '@tanstack/react-start'

// Optional Python sidecar (mean-variance optimization via skfolio). The core
// app runs fine without it — every failure path returns { available: false }
// so the UI degrades to a "sidecar offline" notice instead of crashing.
const SIDECAR_URL = process.env.PORTFOLIO_SIDECAR_URL ?? 'http://localhost:8000'
const TIMEOUT_MS = 30_000

export interface Holding {
  symbol: string
  value?: number
  shares?: number
}

export interface PortfolioMetrics {
  weights: Record<string, number>
  expected_return: number
  volatility: number
  sharpe: number
}

export interface FrontierPoint {
  expected_return: number
  volatility: number
  sharpe: number
}

export interface OptimizeResult {
  asof: string
  objective: 'max_sharpe' | 'min_risk'
  lookback_days: number
  symbols: string[]
  optimal: PortfolioMetrics
  current: PortfolioMetrics | null
  efficient_frontier: FrontierPoint[]
  warnings: string[]
  disclaimer: string
}

interface OptimizeInput {
  holdings: Holding[]
  objective?: 'max_sharpe' | 'min_risk'
  lookbackDays?: number
}

export type OptimizeResponse =
  | { available: true; result: OptimizeResult }
  | { available: false; reason: string }

export const optimizePortfolio = createServerFn({ method: 'POST' })
  .validator((input: OptimizeInput) => input)
  .handler(async ({ data }): Promise<OptimizeResponse> => {
    const holdings = data.holdings
      .map((h) => ({ ...h, symbol: h.symbol.trim().toUpperCase() }))
      .filter((h) => h.symbol)
    if (holdings.length === 0) {
      return { available: false, reason: 'No symbols provided.' }
    }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
    try {
      const res = await fetch(`${SIDECAR_URL}/optimize`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          holdings,
          objective: data.objective ?? 'max_sharpe',
          lookback_days: data.lookbackDays ?? 730,
        }),
        signal: controller.signal,
      })
      if (!res.ok) {
        const detail = await res.text().catch(() => '')
        return { available: false, reason: `Sidecar error ${res.status}: ${detail.slice(0, 300)}` }
      }
      return { available: true, result: (await res.json()) as OptimizeResult }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      const offline = /abort|ECONNREFUSED|fetch failed|network/i.test(msg)
      return {
        available: false,
        reason: offline
          ? `Portfolio sidecar unreachable at ${SIDECAR_URL}. Start it with: cd sidecar && docker compose up`
          : msg,
      }
    } finally {
      clearTimeout(timer)
    }
  })
