// Run via `nr research <SYMBOL>` or `nr discover "<goal>"` (tsx auto-loads .env).
// OPENROUTER_API_KEY must be set; FINNHUB_API_KEY is optional.
//   --cheap     critic on workhorse, skip revision (cheaper, lower quality)
//   --thorough  full premium critic chain (default)
//   --fresh     bypass the data + report caches for this run
import { researchTickerCached } from "./llm/cached-research.js";
import { discoverIdeas } from "./llm/discovery.js";
import type { ReviewMode, Critique } from "./llm/critic.js";
import type { Candidate } from "./llm/discovery.js";
import { formatEvent, formatRollup, withRun } from "./llm/usage.js";
import type { TickerReport } from "./schema.js";
import type { TickerData } from "./data/index.js";

function fmtNum(n: number | undefined): string {
  if (n === undefined) return "n/a";
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function fmtCap(n: number | undefined): string {
  if (n === undefined) return "n/a";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${fmtNum(n)}`;
}

function printReport(data: TickerData, report: TickerReport): void {
  const lines: string[] = [];
  const q = data.quote;

  lines.push("");
  lines.push(`${report.companyName} (${report.symbol})  ·  confidence: ${report.confidence}`);
  lines.push("=".repeat(60));

  if (q) {
    const sign = q.change >= 0 ? "+" : "";
    lines.push(
      `Price: ${fmtNum(q.price)} ${q.currency}   ${sign}${fmtNum(q.change)} (${sign}${fmtNum(q.changePercent)}%)`,
    );
  } else {
    lines.push("Price: not available");
  }
  lines.push(`Fetched: ${data.fetchedAt}`);
  lines.push("");

  lines.push("SUMMARY");
  lines.push(report.summary);
  lines.push("");

  lines.push("KEY METRICS");
  for (const m of report.keyMetrics) {
    lines.push(`  • ${m.label}: ${m.value}`);
    lines.push(`      ${m.interpretation}`);
  }
  lines.push("");

  lines.push("BULL CASE");
  for (const b of report.thesis.bull) lines.push(`  + ${b}`);
  lines.push("");
  lines.push("BEAR CASE");
  for (const b of report.thesis.bear) lines.push(`  - ${b}`);
  lines.push("");

  lines.push("VALUATION CONTEXT");
  lines.push(report.valuationContext);
  lines.push("");

  lines.push("RISKS");
  for (const r of report.risks) lines.push(`  • ${r}`);
  lines.push("");

  lines.push("THINGS TO INVESTIGATE");
  for (const t of report.thingsToInvestigate) lines.push(`  • ${t}`);
  lines.push("");

  if (data.news.length > 0) {
    lines.push("RECENT NEWS");
    for (const n of data.news) {
      lines.push(`  • ${n.title} (${n.source}, ${n.publishedAt})`);
      lines.push(`    ${n.url}`);
    }
    lines.push("");
  }

  console.log(lines.join("\n"));
}

function printCritique(critique: Critique, revised: boolean): void {
  const lines: string[] = [];
  lines.push("CRITIC REVIEW" + (revised ? "  (report was revised)" : ""));
  lines.push("-".repeat(60));
  lines.push(
    `Fabrication check: ${critique.fabricationCheck.passed ? "PASSED" : "FAILED"} — ${critique.fabricationCheck.details}`,
  );
  lines.push(`Suggested confidence: ${critique.suggestedConfidence}`);
  if (critique.issues.length > 0) {
    lines.push("Issues:");
    for (const i of critique.issues) {
      lines.push(`  [${i.severity}] ${i.field}: ${i.problem}`);
      lines.push(`      fix: ${i.fix}`);
    }
  } else {
    lines.push("Issues: none");
  }
  lines.push(`Assessment: ${critique.overallAssessment}`);
  lines.push("");
  console.log(lines.join("\n"));
}

function printCandidates(result: { goal: string; interpretation: string; candidates: Candidate[] }): void {
  const lines: string[] = [];
  lines.push("");
  lines.push(`DISCOVERY · goal: ${result.goal}`);
  lines.push("=".repeat(60));
  lines.push(`Interpreted as: ${result.interpretation}`);
  lines.push("");

  if (result.candidates.length === 0) {
    lines.push("No candidates survived validation and filtering.");
  } else {
    for (const c of result.candidates) {
      lines.push(
        `  ${c.symbol} — ${c.name}   cap: ${fmtCap(c.marketCap)}  P/E: ${fmtNum(c.peRatio)}  [${c.source}]`,
      );
      lines.push(`      ${c.rationale}`);
    }
    lines.push("");
    lines.push(`Next: nr research <SYMBOL> to deep-dive any of these.`);
  }
  lines.push("");
  console.log(lines.join("\n"));
}

async function runResearch(
  symbol: string,
  opts: { mode: ReviewMode; fresh: boolean },
): Promise<void> {
  const ticker = symbol.toUpperCase();
  console.error(`Researching ${ticker} (${opts.mode}${opts.fresh ? ", fresh" : ""})...`);

  const { ticker: data, report, critique, revised } = await withRun(
    { kind: "research", subject: ticker, mode: opts.mode },
    () => researchTickerCached(ticker, opts),
    (event) => console.error("\n" + formatEvent(event)),
  );

  printReport(data, report);
  printCritique(critique, revised);
}

async function runDiscover(goal: string): Promise<void> {
  console.error(`Discovering ideas for: ${goal}`);
  const result = await withRun(
    { kind: "discover", subject: goal },
    () => discoverIdeas(goal),
    (event) => console.error("\n" + formatEvent(event)),
  );
  printCandidates(result);
}

async function main(): Promise<void> {
  const raw = process.argv.slice(2);
  const flags = new Set(raw.filter((a) => a.startsWith("--")));
  const args = raw.filter((a) => !a.startsWith("--"));
  const command = args[0];

  const mode: ReviewMode = flags.has("--cheap") ? "cheap" : "thorough";
  const fresh = flags.has("--fresh");

  if (command === "research") {
    const symbol = args[1];
    if (!symbol) {
      console.error("Usage: nr research <SYMBOL> [--cheap] [--fresh]   (e.g. nr research AAPL)");
      process.exit(1);
    }
    await runResearch(symbol, { mode, fresh });
  } else if (command === "discover") {
    const goal = args.slice(1).join(" ").trim();
    if (!goal) {
      console.error('Usage: nr discover "<goal>"   (e.g. nr discover "AI infrastructure under $100B")');
      process.exit(1);
    }
    await runDiscover(goal);
  } else if (command === "usage") {
    console.log(formatRollup());
  } else {
    console.error(
      'Usage:\n  nr research <SYMBOL> [--cheap] [--fresh]\n  nr discover "<goal>"\n  nr usage',
    );
    process.exit(1);
  }
}

main().catch((err: unknown) => {
  const message = err instanceof Error ? err.message : String(err);
  console.error(`\nError: ${message}`);
  process.exit(1);
});
