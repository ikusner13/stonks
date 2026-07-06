import { useQuery } from "@tanstack/react-query";
import { ApiError } from "./errors";
import { client } from "./client";
import { queryKeys } from "./queryKeys";

function unwrap<T>(data: T | undefined, error: unknown, response: Response): T {
  if (error) throw new ApiError(response.status, error);
  if (data === undefined) throw new ApiError(response.status, { message: "Empty response." });
  return data;
}

export function useMetaQuery() {
  return useQuery({
    queryKey: queryKeys.meta.all,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/meta");
      return unwrap(data, error, response);
    },
  });
}

export function useResearchQuery(symbol: string, mode: string, profile: "none" | "penny" | "largecap", fresh: number) {
  return useQuery({
    queryKey: queryKeys.research.detail(symbol.toUpperCase(), mode, profile, fresh),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/research/{symbol}", {
        params: {
          path: { symbol: symbol.toUpperCase() },
          query: {
            mode,
            fresh,
            profile: profile === "none" ? undefined : profile,
          },
        },
      });
      return unwrap(data, error, response);
    },
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useWatchlistQuery() {
  return useQuery({
    queryKey: queryKeys.watchlist.all,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/watchlist");
      return unwrap(data, error, response);
    },
  });
}

export function usePortfolioSummaryQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.summary(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio");
      return unwrap(data, error, response);
    },
  });
}

export function useHoldingsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.holdings(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/holdings");
      return unwrap(data, error, response);
    },
  });
}

export function useTransactionsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.transactions(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/transactions");
      return unwrap(data, error, response);
    },
  });
}

export function useTargetsQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.targets(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/targets");
      return unwrap(data, error, response);
    },
  });
}

export function useRebalanceQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.rebalance(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/rebalance");
      return unwrap(data, error, response);
    },
  });
}

export function useNavQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.nav(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/nav");
      return unwrap(data, error, response);
    },
  });
}

export function useCorrelationQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.correlation(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/correlation");
      return unwrap(data, error, response);
    },
  });
}

export function useRegimeQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.regime(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/regime");
      return unwrap(data, error, response);
    },
  });
}

export function useTaxQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.tax(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/tax");
      return unwrap(data, error, response);
    },
  });
}

export function usePerformanceQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.performance(),
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/portfolio/performance");
      return unwrap(data, error, response);
    },
  });
}

export function useTwrQuery() {
  return useQuery({
    queryKey: queryKeys.portfolio.twr(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/portfolio/twr");
      if (error) throw new ApiError(500, error);
      return data ?? null;
    },
  });
}
