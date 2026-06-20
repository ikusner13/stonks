import { generateObject, type TextPart } from "ai";
import { z } from "zod";
import type { TickerData } from "../data/index.js";
import { TickerReportSchema, type TickerReport } from "../schema.js";
import { premium, workhorse } from "./provider.js";
import { researchTicker } from "./research.js";
import { annotateRun, tracked, type CallSite } from "./usage.js";

export type ReviewMode = "thorough" | "cheap";

export interface Critique {
  fabricationCheck: { passed: boolean; details: string };
  issues: {
    severity: "low" | "medium" | "high";
    field: string;
    problem: string;
    fix: string;
  }[];
  suggestedConfidence: "low" | "medium" | "high";
  overallAssessment: string;
}

const CritiqueSchema = z.object({
  fabricationCheck: z
    .object({
      passed: z
        .boolean()
        .describe("True if every number in the report is traceable to the ground-truth data."),
      details: z
        .string()
        .describe("Explanation of the fabrication assessment, naming any untraceable figures."),
    })
    .describe("Verdict on whether the report invents numbers not present in the ground truth."),
  issues: z
    .array(
      z.object({
        severity: z
          .enum(["low", "medium", "high"])
          .describe("How serious the issue is: high means a fabrication or materially misleading claim."),
        field: z
          .string()
          .describe("The report field the issue concerns, e.g. 'thesis.bull' or 'keyMetrics[2].value'."),
        problem: z.string().describe("What is wrong with this part of the report."),
        fix: z.string().describe("Concrete instruction for how to correct the problem."),
      }),
    )
    .describe("Specific, actionable problems found in the report. Empty if none."),
  suggestedConfidence: z
    .enum(["low", "medium", "high"])
    .describe("The confidence the report SHOULD state given the completeness of the data."),
  overallAssessment: z
    .string()
    .describe("A brief skeptical summary of the report's quality and trustworthiness."),
});

const MAGNITUDE: Record<string, number> = {
  k: 1e3, m: 1e6, b: 1e9, t: 1e12,
  thousand: 1e3, million: 1e6, billion: 1e9, trillion: 1e12,
};

// Returns candidate interpretations per numeric token. Handles $, commas,
// decimals, a % sign, an adjacent magnitude letter (B/M), and a spelled-out
// magnitude word ("$19.83 billion"). \d+ (not \d{1,3}) so a raw uncommatted
// integer like 4376979046400 matches whole. A "25%" token yields both 25 and
// 0.25 since ratios may be stored as fractions; either match grounds it.
function parseNumbers(text: string): number[][] {
  const out: number[][] = [];
  const re =
    /-?\$?(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|[kmbt]\b|thousand|million|billion|trillion)?/gi;
  for (const match of text.matchAll(re)) {
    let n = Number(match[1]!.replace(/,/g, ""));
    if (!Number.isFinite(n)) continue;
    const unit = match[2]?.toLowerCase();
    if (unit && unit !== "%" && unit in MAGNITUDE) n *= MAGNITUDE[unit]!;
    out.push(unit === "%" ? [n, n / 100] : [n]);
  }
  return out;
}

// Every number appearing anywhere in the ground truth — quote, fundamentals, and
// figures quoted in news HEADLINES — is fair for the report to restate. A flag
// therefore means the figure appears nowhere in the data we provided. (Timestamps
// and URLs are excluded to avoid spurious date-fragment matches.)
function collectAllowed(data: TickerData): number[] {
  const parts: string[] = [JSON.stringify(data.fundamentals)];
  if (data.quote) parts.push(JSON.stringify(data.quote));
  for (const n of data.news) parts.push(n.title);
  return parts.flatMap((p) => parseNumbers(p)).flat();
}

// A report number is grounded if it matches an allowed number within a small
// relative tolerance, absorbing rounding/unit restatements (e.g. 2.5T vs 2.49e12).
function isGrounded(n: number, allowed: number[], tol = 0.02): boolean {
  const an = Math.abs(n); // sign is conveyed by words ("down 0.61%"), compare magnitude
  for (const a of allowed) {
    const aa = Math.abs(a);
    const scale = Math.max(aa, an);
    if (scale === 0) return true;
    if (Math.abs(aa - an) / scale <= tol) return true;
  }
  return false;
}

export function checkFabrication(
  report: TickerReport,
  data: TickerData,
): { passed: boolean; details: string } {
  const allowed = collectAllowed(data);

  const candidates: { source: string; values: number[] }[] = [];
  for (const [i, m] of report.keyMetrics.entries()) {
    for (const values of parseNumbers(m.value)) {
      candidates.push({ source: `keyMetrics[${i}] (${m.label})`, values });
    }
  }
  for (const values of parseNumbers(report.valuationContext)) {
    candidates.push({ source: "valuationContext", values });
  }

  const unmatched = candidates.filter(
    (c) => !c.values.some((v) => isGrounded(v, allowed)),
  );
  if (unmatched.length === 0) {
    return { passed: true, details: "All report figures trace to the provided data." };
  }
  const list = unmatched.map((c) => `${c.values[0]} in ${c.source}`).join("; ");
  return {
    passed: false,
    details: `Figures not found in ground-truth data (within tolerance): ${list}.`,
  };
}

// ONE shared system for every critic-chain call (critique / revise / re-critique).
// Keeping the system identical across calls 2–4 is what lets the ground-truth
// block below sit in a single cacheable prefix shared by all three (W2). The
// per-call task (audit vs revise) lives in the variable tail, after the cache
// breakpoint — so it never invalidates the cache.
const CRITIC_SYSTEM = `You are a skeptical senior equity-research analyst working against a fixed ground-truth JSON dataset. You perform exactly one task per request — AUDIT a report, or REVISE a report after an audit — as stated in the user message.

ABSOLUTE RULES (both tasks):
- You may ONLY reason over the numeric figures explicitly present in the ground-truth JSON.
- NEVER invent, estimate, extrapolate, or recall from memory any number (prices, ratios, market cap, dates).
- In keyMetrics, "value" must be a figure RESTATED verbatim from the JSON.
- If a figure is missing or null, say it is not available rather than guessing.
- Default to skepticism. Sparse or null-heavy data should mean lower confidence.

Follow the task instructions in the user message exactly.`;

// The stable, repeated, token-heavy prefix. Marked with an OpenRouter ephemeral
// cache breakpoint so calls 2–4 read it from cache instead of re-paying for it.
// Built ONCE per reviewed run so the bytes are identical across calls.
function groundTruthPart(data: TickerData): TextPart {
  return {
    type: "text",
    text: `GROUND TRUTH (the ONLY numbers you may use):
\`\`\`json
${JSON.stringify(data, null, 2)}
\`\`\``,
    providerOptions: { openrouter: { cacheControl: { type: "ephemeral", ttl: "1h" } } },
  };
}

const AUDIT_GUIDANCE = `Audit for: support (is every qualitative claim grounded?), traceability (is every number present in the ground truth? treat the fabrication hint as authoritative for hard fabrications, then look for any it missed), balance (fair bull/bear?), completeness (material risks omitted?), calibration (confidence justified by data completeness?). Report concrete, actionable issues; reserve "high" severity for fabricated numbers or materially misleading claims.`;

async function runCritique(
  report: TickerReport,
  data: TickerData,
  gt: TextPart,
  callSite: CallSite,
  mode: ReviewMode,
): Promise<Critique> {
  const fab = checkFabrication(report, data);

  const tail: TextPart = {
    type: "text",
    text: `TASK: AUDIT the report below against the ground truth above.

PROGRAMMATIC FABRICATION CHECK (heuristic hint): ${fab.passed ? "PASSED" : `FAILED — ${fab.details}`}

${AUDIT_GUIDANCE}

REPORT UNDER REVIEW:
\`\`\`json
${JSON.stringify(report, null, 2)}
\`\`\``,
  };

  const { object } = await tracked(callSite, () =>
    generateObject({
      model: mode === "cheap" ? workhorse() : premium(),
      schema: CritiqueSchema,
      system: CRITIC_SYSTEM,
      messages: [{ role: "user", content: [gt, tail] }],
    }),
  );

  // Never let the LLM override a hard-caught fabrication.
  if (!fab.passed) {
    return {
      ...object,
      fabricationCheck: {
        passed: false,
        details: `${fab.details} ${object.fabricationCheck.details}`.trim(),
      },
    };
  }
  return object;
}

// Public single-shot critique (used by tests / direct callers). Builds its own
// ground-truth part; caching benefit only accrues across the reviewed chain.
export async function critiqueReport(report: TickerReport, data: TickerData): Promise<Critique> {
  return runCritique(report, data, groundTruthPart(data), "critique", "thorough");
}

export async function researchTickerReviewed(
  symbol: string,
  data: TickerData,
  opts?: { mode?: ReviewMode },
): Promise<{ report: TickerReport; critique: Critique; revised: boolean }> {
  const mode = opts?.mode ?? "thorough";
  const gt = groundTruthPart(data); // one cacheable prefix shared by every call below

  const report = await researchTicker(symbol, data);
  const critique = await runCritique(report, data, gt, "critique", mode);

  // Cheap mode stops here: workhorse critique, no premium revision (W4).
  const needsRevision =
    mode === "thorough" &&
    (!critique.fabricationCheck.passed ||
      critique.issues.some((i) => i.severity === "medium" || i.severity === "high"));

  if (!needsRevision) {
    annotateRun({ revised: false });
    return { report, critique, revised: false };
  }

  const reviseTail: TextPart = {
    type: "text",
    text: `TASK: REVISE the report for ${symbol} to fix every problem found in the audit. Apply each fix. Remove any fabricated or unsupported figure entirely rather than replacing it with another guess. Keep the report balanced and grounded.

ORIGINAL REPORT:
\`\`\`json
${JSON.stringify(report, null, 2)}
\`\`\`

AUDIT ISSUES TO FIX:
\`\`\`json
${JSON.stringify(critique.issues, null, 2)}
\`\`\``,
  };

  const { object: revisedReport } = await tracked("revise", () =>
    generateObject({
      model: premium(),
      schema: TickerReportSchema,
      system: CRITIC_SYSTEM,
      messages: [{ role: "user", content: [gt, reviseTail] }],
    }),
  );

  const finalCritique = await runCritique(revisedReport, data, gt, "re-critique", mode);
  annotateRun({ revised: true });
  return { report: revisedReport, critique: finalCritique, revised: true };
}
