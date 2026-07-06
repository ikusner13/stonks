import { describe, expect, it } from "vitest";
import { optimizerWeightsToTargets } from "./optimizer";
import type { components } from "@/api/schema";

type OptimizeResult = components["schemas"]["OptimizeResult"];

describe("optimizerWeightsToTargets", () => {
  it("converts optimizer decimal weights to sorted percent target inputs", () => {
    const result: OptimizeResult = {
      asof: "2026-07-06",
      objective: "max_sharpe",
      lookback_days: 252,
      symbols: ["MSFT", "AAPL", "CASH"],
      optimal: {
        weights: { msft: 0.253456, AAPL: 0.5, ZERO: 0 },
        expected_return: 0.1,
        volatility: 0.2,
        sharpe: 0.5,
      },
      current: null,
      efficient_frontier: [],
      warnings: [],
      disclaimer: "Research context only.",
    };

    expect(optimizerWeightsToTargets(result)).toEqual([
      { symbol: "AAPL", weight_pct: 50 },
      { symbol: "MSFT", weight_pct: 25.3456 },
    ]);
  });
});
