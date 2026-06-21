import { describe, expect, it } from 'vitest'
import { checkFabrication } from './critic.js'
import type { TickerData } from '../data/index.js'
import type { TickerReport } from '../schema.js'

const data: TickerData = {
  symbol: 'AAPL',
  fetchedAt: '2026-06-21T00:00:00Z',
  quote: { price: 201.5, currency: 'USD', change: -1.23, changePercent: -0.61 },
  fundamentals: {
    marketCap: 3.05e12,
    peRatio: 32.4,
    forwardPe: 28.1,
    profitMargin: 0.25,
    revenue: 3.9e11,
  },
  news: [
    { title: 'Apple ships 19.83 billion units', url: 'http://x', publishedAt: '', source: 'wire' },
  ],
}

function report(over: Partial<TickerReport>): TickerReport {
  return {
    symbol: 'AAPL',
    companyName: 'Apple Inc.',
    summary: 'A summary.',
    thesis: { bull: [], bear: [] },
    keyMetrics: [],
    valuationContext: '',
    risks: [],
    thingsToInvestigate: [],
    confidence: 'medium',
    ...over,
  }
}

describe('checkFabrication', () => {
  it('passes when every figure traces to the ground truth', () => {
    const r = report({
      keyMetrics: [
        { label: 'P/E', value: '32.4', interpretation: '' },
        { label: 'Market cap', value: '$3.05T', interpretation: '' },
        { label: 'Price', value: '201.5', interpretation: '' },
      ],
      valuationContext: 'A forward P/E of 28.1 looks rich.',
    })
    expect(checkFabrication(r, data).passed).toBe(true)
  })

  it('passes a report with no numeric claims', () => {
    expect(checkFabrication(report({}), data).passed).toBe(true)
  })

  it('flags a keyMetric figure absent from the ground truth', () => {
    const r = report({ keyMetrics: [{ label: 'PEG', value: '1.87', interpretation: '' }] })
    const res = checkFabrication(r, data)
    expect(res.passed).toBe(false)
    expect(res.details).toContain('1.87')
  })

  it('flags a fabricated figure in valuationContext', () => {
    const r = report({ valuationContext: 'Trading at a wild 99.9 P/E.' })
    const res = checkFabrication(r, data)
    expect(res.passed).toBe(false)
    expect(res.details).toContain('99.9')
  })

  it('grounds a percentage stated against a stored fraction (25% vs 0.25)', () => {
    const r = report({ keyMetrics: [{ label: 'Margin', value: '25%', interpretation: '' }] })
    expect(checkFabrication(r, data).passed).toBe(true)
  })

  it('grounds a spelled-out magnitude within tolerance (19.83 billion vs $19.8B)', () => {
    const r = report({ keyMetrics: [{ label: 'Units', value: '$19.8B', interpretation: '' }] })
    expect(checkFabrication(r, data).passed).toBe(true)
  })

  it('treats numbers quoted in news headlines as allowed', () => {
    const r = report({ keyMetrics: [{ label: 'Units', value: '19.83 billion', interpretation: '' }] })
    expect(checkFabrication(r, data).passed).toBe(true)
  })
})
