import { generateObject } from "ai"
import { z } from "zod"
import { fetchTickerData } from "../data/index.js"
import { runScreen, SCREEN_IDS, type ScreenId } from "../data/screener.js"
import { workhorse } from "./provider.js"
import { tracked } from "./usage.js"

// Discovery is non-critical: chase the cheapest provider endpoint (W4).
const CHEAP_ROUTE = { provider: { sort: "price" as const } }

export interface Candidate {
  symbol: string
  name: string
  marketCap?: number
  peRatio?: number
  rationale: string
  source: "screener" | "theme"
}

const ScreenPlanSchema = z.object({
  strategy: z
    .enum([...SCREEN_IDS, "none"] as unknown as [string, ...string[]])
    .describe(
      "Which predefined Yahoo screen best matches the goal, or \"none\" if no predefined screen fits and the goal is purely thematic/qualitative.",
    ),
  themeCandidates: z
    .array(z.string())
    .describe(
      "Ticker symbols you believe fit a thematic or qualitative goal that no predefined screen captures (e.g. specific AI-infrastructure names). May be empty when a strategy is used.",
    ),
  filters: z
    .object({
      maxMarketCap: z
        .number()
        .nullable()
        .optional()
        .describe("Maximum market cap in absolute dollars (e.g. 100e9 for \"$100B\"), or null."),
      minMarketCap: z
        .number()
        .nullable()
        .optional()
        .describe("Minimum market cap in absolute dollars, or null."),
      maxPe: z.number().nullable().optional().describe("Maximum trailing P/E ratio, or null."),
    })
    .describe("Numeric constraints extracted from the goal. These are ENFORCED IN CODE against real data, not by you."),
  interpretation: z.string().describe("One-sentence restatement of how you understood the goal."),
})

type ScreenPlan = z.infer<typeof ScreenPlanSchema>

const PLAN_SYSTEM = `You are an equity-idea-discovery assistant. Given an investment goal, you propose which ONE predefined Yahoo screen best fits and/or name specific companies for a qualitative theme.

ABSOLUTE RULES:
- NEVER fabricate financial figures (market cap, P/E, price). Every number is independently fetched and validated downstream; invented numbers are useless.
- For "filters", only extract numeric thresholds the user actually expressed in the goal; convert market cap to absolute dollars (e.g. "$100B" -> 100e9). Use null when not specified.
- Prefer a predefined "strategy" when one cleanly matches; use themeCandidates for goals no screen captures.
- Treat the goal as the user's private research intent.`

const RATIONALE_SYSTEM = `You write one-sentence, qualitative rationales explaining why each stock fits the user's research goal.

ABSOLUTE RULES:
- NEVER invent or restate specific numbers (prices, ratios, market cap). Keep rationales purely qualitative.
- Ground each rationale in the goal and the stock's role/business; do not fabricate facts.`

async function safe<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn()
  } catch {
    return null
  }
}

function passesFilters(filters: ScreenPlan["filters"], marketCap?: number, peRatio?: number): boolean {
  if (filters.maxMarketCap != null && (marketCap == null || marketCap > filters.maxMarketCap)) return false
  if (filters.minMarketCap != null && (marketCap == null || marketCap < filters.minMarketCap)) return false
  if (filters.maxPe != null && (peRatio == null || peRatio > filters.maxPe)) return false
  return true
}

export async function discoverIdeas(
  goal: string,
  opts?: { limit?: number },
): Promise<{ goal: string; interpretation: string; candidates: Candidate[] }> {
  const limit = opts?.limit ?? 8

  const { object: plan } = await tracked("discover-plan", () =>
    generateObject({
      model: workhorse(CHEAP_ROUTE),
      schema: ScreenPlanSchema,
      system: PLAN_SYSTEM,
      prompt: `Investment goal:\n${goal}\n\nPropose a screening plan.`,
    }),
  )

  // Gather raw candidate symbols with provenance (and any name the screener provided).
  const sources = new Map<string, { source: "screener" | "theme"; name?: string }>()
  if (plan.strategy !== "none") {
    const screened = await runScreen(plan.strategy as ScreenId)
    for (const q of screened) if (!sources.has(q.symbol)) sources.set(q.symbol, { source: "screener", name: q.name })
  }
  for (const sym of plan.themeCandidates) {
    const s = sym.trim().toUpperCase()
    if (s && !sources.has(s)) sources.set(s, { source: "theme" })
  }

  // Validate + enrich each unique symbol against real data; enforce filters in code.
  const enriched = await Promise.all(
    [...sources.entries()].map(async ([symbol, { source, name }]) => {
      const data = await safe(() => fetchTickerData(symbol))
      if (!data) return null
      const marketCap = data.fundamentals.marketCap
      const peRatio = data.fundamentals.peRatio
      // Drop likely-hallucinated/delisted symbols: no usable quote or market cap.
      if (data.quote == null && marketCap == null) return null
      if (!passesFilters(plan.filters, marketCap, peRatio)) return null
      return { symbol, source, name: name ?? symbol, marketCap, peRatio }
    }),
  )

  const survivors = enriched.filter((c): c is NonNullable<typeof c> => c != null).slice(0, limit)

  if (survivors.length === 0) {
    return { goal, interpretation: plan.interpretation, candidates: [] }
  }

  // Have the LLM attach qualitative rationales for the validated survivors.
  const RationaleSchema = z.object({
    rationales: z.array(
      z.object({
        symbol: z.string(),
        rationale: z.string().describe("One qualitative sentence on why this stock fits the goal."),
      }),
    ),
  })

  const { object: rat } = await tracked("discover-rationale", () =>
    generateObject({
      model: workhorse(CHEAP_ROUTE),
      schema: RationaleSchema,
      system: RATIONALE_SYSTEM,
      prompt: `Goal:\n${goal}\n\nWrite a one-sentence qualitative rationale for each of these validated tickers:\n${survivors
        .map((s) => `- ${s.symbol} (source: ${s.source})`)
        .join("\n")}`,
    }),
  )

  const rationaleBy = new Map(rat.rationales.map((r) => [r.symbol.toUpperCase(), r.rationale]))

  const candidates: Candidate[] = survivors.map((s) => {
    const c: Candidate = {
      symbol: s.symbol,
      name: s.name,
      rationale:
        rationaleBy.get(s.symbol.toUpperCase()) ??
        `Surfaced via ${s.source} as a fit for the stated goal.`,
      source: s.source,
    }
    if (s.marketCap != null) c.marketCap = s.marketCap
    if (s.peRatio != null) c.peRatio = s.peRatio
    return c
  })

  return { goal, interpretation: plan.interpretation, candidates }
}
