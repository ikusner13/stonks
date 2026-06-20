import { fetchTickerData, type TickerData } from "../data/index.js";
import { withCache } from "../lib/cache.js";
import type { TickerReport } from "../schema.js";
import { researchTickerReviewed, type Critique, type ReviewMode } from "./critic.js";
import { annotateRun } from "./usage.js";

export interface ResearchResult {
  ticker: TickerData;
  report: TickerReport;
  critique: Critique;
  revised: boolean;
}

// Reports are stable for a trading day; re-opening one (CLI re-run or web
// revisit) should cost $0. Long TTL — the symbol+day key already scopes it.
const REPORT_TTL_MS = 24 * 60 * 60_000;

function tradingDay(): string {
  return new Date().toISOString().slice(0, 10); // UTC date; good enough for a personal tool
}

// Full pipeline with a persistent result cache keyed by symbol+day+mode.
// A cache hit makes ZERO LLM and (via the data cache) zero network calls.
export async function researchTickerCached(
  symbol: string,
  opts?: { mode?: ReviewMode; fresh?: boolean },
): Promise<ResearchResult> {
  const sym = symbol.toUpperCase();
  const mode = opts?.mode ?? "thorough";
  const key = `${sym}:${tradingDay()}:${mode}`;

  const { value, hit } = await withCache<ResearchResult>(
    "report",
    key,
    REPORT_TTL_MS,
    async () => {
      const ticker = await fetchTickerData(sym, { fresh: opts?.fresh });
      const { report, critique, revised } = await researchTickerReviewed(sym, ticker, { mode });
      return { ticker, report, critique, revised };
    },
    { fresh: opts?.fresh },
  );

  if (hit) annotateRun({ cached: true, revised: value.revised });
  return value;
}
