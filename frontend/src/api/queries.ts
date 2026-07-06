import { useQuery } from "@tanstack/react-query";
import { req } from "./errors";
import { client } from "./client";
import { queryKeys } from "./queryKeys";

export function useMetaQuery() {
  return useQuery({
    queryKey: queryKeys.meta.all,
    queryFn: () => req("GET /api/meta", () => client.GET("/api/meta")),
  });
}

export function useResearchQuery(symbol: string, mode: string, profile: "none" | "penny" | "largecap", fresh: number) {
  return useQuery({
    queryKey: queryKeys.research.detail(symbol.toUpperCase(), mode, profile, fresh),
    queryFn: () =>
      req(`GET /api/research/${symbol.toUpperCase()}`, () =>
        client.GET("/api/research/{symbol}", {
          params: {
            path: { symbol: symbol.toUpperCase() },
            query: {
              mode,
              fresh,
              profile: profile === "none" ? undefined : profile,
            },
          },
        }),
      ),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useWatchlistQuery() {
  return useQuery({
    queryKey: queryKeys.watchlist.all,
    queryFn: () => req("GET /api/watchlist", () => client.GET("/api/watchlist")),
  });
}

export function usePortfolioSummaryQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.summary(),
    queryFn: () => req("GET /api/portfolio", () => client.GET("/api/portfolio")),
  });
}

export function useHoldingsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.holdings(),
    queryFn: () => req("GET /api/portfolio/holdings", () => client.GET("/api/portfolio/holdings")),
  });
}

export function useTransactionsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.transactions(),
    queryFn: () => req("GET /api/portfolio/transactions", () => client.GET("/api/portfolio/transactions")),
  });
}

export function useTargetsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.targets(),
    queryFn: () => req("GET /api/portfolio/targets", () => client.GET("/api/portfolio/targets")),
  });
}

export function useRebalanceQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.rebalance(),
    queryFn: () => req("GET /api/portfolio/rebalance", () => client.GET("/api/portfolio/rebalance")),
  });
}

export function useNavQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.nav(),
    queryFn: () => req("GET /api/portfolio/nav", () => client.GET("/api/portfolio/nav")),
  });
}

export function useCorrelationQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.correlation(),
    queryFn: () => req("GET /api/portfolio/correlation", () => client.GET("/api/portfolio/correlation")),
  });
}

export function useRegimeQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.regime(),
    queryFn: () => req("GET /api/portfolio/regime", () => client.GET("/api/portfolio/regime")),
  });
}

export function useTaxQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.tax(),
    queryFn: () => req("GET /api/portfolio/tax", () => client.GET("/api/portfolio/tax")),
  });
}

export function usePerformanceQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.performance(),
    queryFn: () => req("GET /api/portfolio/performance", () => client.GET("/api/portfolio/performance")),
  });
}

export function useTwrQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.twr(),
    queryFn: () => req("GET /api/portfolio/twr", () => client.GET("/api/portfolio/twr")),
  });
}
