// Query key hierarchy. Keep every portfolio-scoped key nested under the
// `portfolio` prefix so a single `invalidateQueries({ queryKey: queryKeys.portfolio.all })`
// after a mutation (buy/sell/import) busts every portfolio view at once.
export const queryKeys = {
  portfolio: {
    all: ["portfolio"] as const,
    summary: () => [...queryKeys.portfolio.all, "summary"] as const,
    holdings: () => [...queryKeys.portfolio.all, "holdings"] as const,
    transactions: () => [...queryKeys.portfolio.all, "transactions"] as const,
    targets: () => [...queryKeys.portfolio.all, "targets"] as const,
    rebalance: () => [...queryKeys.portfolio.all, "rebalance"] as const,
    whatif: (amount: number) => [...queryKeys.portfolio.all, "whatif", amount] as const,
    nav: () => [...queryKeys.portfolio.all, "nav"] as const,
    correlation: () => [...queryKeys.portfolio.all, "correlation"] as const,
    regime: () => [...queryKeys.portfolio.all, "regime"] as const,
    tax: () => [...queryKeys.portfolio.all, "tax"] as const,
    performance: () => [...queryKeys.portfolio.all, "performance"] as const,
    twr: () => [...queryKeys.portfolio.all, "twr"] as const,
  },
  watchlist: {
    all: ["watchlist"] as const,
  },
  research: {
    all: ["research"] as const,
    detail: (symbol: string, mode: string, profile: string, fresh: number) =>
      [...queryKeys.research.all, symbol, mode, profile, fresh] as const,
  },
  discover: {
    all: ["discover"] as const,
  },
  meta: {
    all: ["meta"] as const,
  },
} as const;
