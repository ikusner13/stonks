import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, apiErrorMessage } from "./errors";
import { client } from "./client";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

type DiscoverRequest = components["schemas"]["DiscoverRequest"];
type HoldingUpdate = components["schemas"]["HoldingUpdate"];
type CashUpdate = components["schemas"]["CashUpdate"];
type TransactionCreate = components["schemas"]["TransactionCreate"];
type TargetsUpdate = components["schemas"]["TargetsUpdate"];
type WhatIfRequest = components["schemas"]["WhatIfRequest"];
type OptimizeApiRequest = components["schemas"]["OptimizeApiRequest"];

function unwrap<T>(data: T | undefined, error: unknown, response: Response): T {
  if (error) throw new ApiError(response.status, error);
  if (data === undefined) throw new ApiError(response.status, { message: "Empty response." });
  return data;
}

function notifyError(error: unknown) {
  toast.error(apiErrorMessage(error));
}

export function useDiscoverMutation() {
  return useMutation({
    mutationFn: async (body: DiscoverRequest) => {
      const { data, error, response } = await client.POST("/api/discover", { body });
      return unwrap(data, error, response);
    },
    onError: notifyError,
  });
}

export function useWatchMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ symbol, watched }: { symbol: string; watched: boolean }) => {
      const path = { symbol: symbol.toUpperCase() };
      const result = watched
        ? await client.DELETE("/api/watchlist/{symbol}", { params: { path } })
        : await client.PUT("/api/watchlist/{symbol}", { params: { path } });
      return unwrap(result.data, result.error, result.response);
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

export function useUpsertHoldingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: HoldingUpdate) => {
      const { data, error, response } = await client.PUT("/api/portfolio/holdings", { body });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.holdings(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useDeleteHoldingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string) => {
      const { data, error, response } = await client.DELETE("/api/portfolio/holdings/{symbol}", {
        params: { path: { symbol: symbol.toUpperCase() } },
      });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.holdings(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useCashMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CashUpdate) => {
      const { data, error, response } = await client.PUT("/api/portfolio/cash", { body });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.holdings(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useImportHoldingsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const { data, error, response } = await client.POST("/api/portfolio/holdings/import", {
        body: formData as unknown as { file: string },
      });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.holdings(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useBrokerSyncMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST("/api/portfolio/broker/sync");
      return unwrap(data, error, response);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useAddTransactionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: TransactionCreate) => {
      const { data, error, response } = await client.POST("/api/portfolio/transactions", { body });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.transactions(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useDeleteTransactionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (txnId: number) => {
      const { data, error, response } = await client.DELETE("/api/portfolio/transactions/{txn_id}", {
        params: { path: { txn_id: txnId } },
      });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.transactions(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useImportTransactionsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const { data, error, response } = await client.POST("/api/portfolio/transactions/import", {
        body: formData as unknown as { file: string },
      });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.transactions(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useTargetsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: TargetsUpdate) => {
      const { data, error, response } = await client.PUT("/api/portfolio/targets", { body });
      return unwrap(data, error, response);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.portfolio.targets(), data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portfolio.all });
    },
    onError: notifyError,
  });
}

export function useWhatIfMutation() {
  return useMutation({
    mutationFn: async (body: WhatIfRequest) => {
      const { data, error, response } = await client.POST("/api/portfolio/whatif", { body });
      return unwrap(data, error, response);
    },
    onError: notifyError,
  });
}

export function useOptimizeMutation() {
  return useMutation({
    mutationFn: async (body: OptimizeApiRequest) => {
      const { data, error, response } = await client.POST("/api/portfolio/optimize", { body });
      return unwrap(data, error, response);
    },
    onError: notifyError,
  });
}
