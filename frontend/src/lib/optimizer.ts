import type { components } from "@/api/schema";

type OptimizeResult = components["schemas"]["OptimizeResult"];
type TargetInput = components["schemas"]["TargetInput"];

export function optimizerWeightsToTargets(result: OptimizeResult): TargetInput[] {
  return Object.entries(result.optimal.weights)
    .filter(([, weight]) => Number.isFinite(weight) && weight > 0)
    .map(([symbol, weight]) => ({
      symbol: symbol.toUpperCase(),
      weight_pct: Number((weight * 100).toFixed(4)),
    }))
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}
