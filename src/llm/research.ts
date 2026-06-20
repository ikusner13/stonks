import { generateObject } from "ai";
import type { TickerData } from "../data/index.js";
import { TickerReportSchema, type TickerReport } from "../schema.js";
import { workhorse } from "./provider.js";
import { tracked } from "./usage.js";

const SYSTEM = `You are a meticulous equity-research analyst. You write neutral, balanced reports.

ABSOLUTE RULES:
- You may ONLY reason over the numeric figures explicitly present in the provided JSON ground truth.
- NEVER invent, estimate, extrapolate, or recall from memory any number (prices, ratios, market cap, dates, etc.).
- In keyMetrics, "value" must be the figure RESTATED verbatim from the JSON. Do not compute new numbers.
- If a figure needed for analysis is missing or null, explicitly say it is not available rather than guessing.
- Qualitative judgement (thesis, risks, interpretation) is encouraged, but it must be grounded in the provided data.`;

export async function researchTicker(
  symbol: string,
  data: TickerData,
): Promise<TickerReport> {
  const prompt = `Produce a structured research report for ${symbol}.

Below is the ONLY ground truth you may use. Treat it as authoritative and complete; do not supplement it with outside knowledge of specific numbers.

\`\`\`json
${JSON.stringify(data, null, 2)}
\`\`\`

Restate the relevant figures in keyMetrics, explain why each matters, and build a balanced bull/bear thesis. Where data is missing, note it in thingsToInvestigate and let it lower your confidence.`;

  const { object } = await tracked("research", () =>
    generateObject({
      model: workhorse(),
      schema: TickerReportSchema,
      system: SYSTEM,
      prompt,
    }),
  );

  return object;
}
