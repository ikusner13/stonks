import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiErrorMessage, req } from "./errors";
import { client } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

type DiscoverRequest = components["schemas"]["DiscoverRequest"];
type TargetsUpdate = components["schemas"]["TargetsUpdate"];
type WhatIfRequest = components["schemas"]["WhatIfRequest"];
type OptimizeApiRequest = components["schemas"]["OptimizeApiRequest"];

function notifyError(error: unknown) {
  toast.error(apiErrorMessage(error));
}

export function useDiscoverMutation() {
  return useMutation({
    mutationFn: (body: DiscoverRequest) =>
      req("POST /api/discover", () => client.POST("/api/discover", { body })),
    onError: notifyError,
  });
}

export function useWatchMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol, watched }: { symbol: string; watched: boolean }) => {
      const path = { symbol: symbol.toUpperCase() };
      return watched
        ? req(`DELETE /api/watchlist/${path.symbol}`, () => client.DELETE("/api/watchlist/{symbol}", { params: { path } }))
        : req(`PUT /api/watchlist/${path.symbol}`, () => client.PUT("/api/watchlist/{symbol}", { params: { path } }));
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.watchlist.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.research.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.discover.all }),
      ]);
    },
    onError: notifyError,
  });
}

export function useBrokerSyncMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      req("POST /api/portfolio/broker/sync", () => client.POST("/api/portfolio/broker/sync")),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useTargetsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TargetsUpdate) =>
      req("PUT /api/portfolio/targets", () => client.PUT("/api/portfolio/targets", { body })),
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.targets(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useWhatIfMutation() {
  return useMutation({
    mutationFn: (body: WhatIfRequest) =>
      req("POST /api/portfolio/whatif", () => client.POST("/api/portfolio/whatif", { body })),
    onError: notifyError,
  });
}

export function useOptimizeMutation() {
  return useMutation({
    mutationFn: (body: OptimizeApiRequest) =>
      req("POST /api/portfolio/optimize", () => client.POST("/api/portfolio/optimize", { body })),
    onError: notifyError,
  });
}
