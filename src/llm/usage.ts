import { AsyncLocalStorage } from "node:async_hooks";
import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

// One canonical wide event per run lands here as JSONL — `nr usage` sums it.
const CACHE_DIR = resolve(process.cwd(), ".cache");
const USAGE_LOG = resolve(CACHE_DIR, "usage.jsonl");

export type CallSite =
  | "research"
  | "critique"
  | "revise"
  | "re-critique"
  | "discover-plan"
  | "discover-rationale";

export interface CallUsage {
  callSite: CallSite;
  model: string;
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number; // input tokens served from cache (W2)
  costUsd: number; // real $ from OpenRouter usage accounting
  durationMs: number;
}

export interface RunEvent {
  ts: string;
  kind: "research" | "discover";
  subject: string; // symbol or goal
  mode: string; // thorough | cheap | …
  cached: boolean; // whole run served from result cache (W3) → zero LLM calls
  revised?: boolean;
  calls: CallUsage[];
  totals: {
    calls: number;
    inputTokens: number;
    outputTokens: number;
    cachedInputTokens: number;
    costUsd: number;
    durationMs: number;
  };
  node: string;
}

interface RunContext {
  kind: RunEvent["kind"];
  subject: string;
  mode: string;
  calls: CallUsage[];
  extra: { cached?: boolean; revised?: boolean };
}

const store = new AsyncLocalStorage<RunContext>();

// Minimal shape we read off a generateObject/generateText result.
interface TrackableResult {
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
    cachedInputTokens?: number;
  };
  providerMetadata?: Record<string, unknown>;
  response?: { modelId?: string };
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function extract(callSite: CallSite, res: TrackableResult, durationMs: number): CallUsage {
  // OpenRouter usage accounting (authoritative for $ and cache hits).
  const or = (res.providerMetadata?.openrouter as { usage?: Record<string, unknown> } | undefined)
    ?.usage;
  const cachedFromOr = num(
    (or?.promptTokensDetails as { cachedTokens?: number } | undefined)?.cachedTokens,
  );

  return {
    callSite,
    model: res.response?.modelId ?? "unknown",
    inputTokens: num(res.usage?.inputTokens) || num(or?.promptTokens),
    outputTokens: num(res.usage?.outputTokens) || num(or?.completionTokens),
    cachedInputTokens: num(res.usage?.cachedInputTokens) || cachedFromOr,
    costUsd: num(or?.cost),
    durationMs,
  };
}

// Time an LLM call, record its usage into the active run (if any), return the
// result unchanged. Call sites stay one line: `await tracked("research", () => generateObject(...))`.
export async function tracked<R extends TrackableResult>(
  callSite: CallSite,
  run: () => Promise<R>,
): Promise<R> {
  const start = Date.now();
  const res = await run();
  const ctx = store.getStore();
  if (ctx) ctx.calls.push(extract(callSite, res, Date.now() - start));
  return res;
}

// Add run-level facts that aren't tied to one call (revised?, cache hit?).
export function annotateRun(fields: { cached?: boolean; revised?: boolean }): void {
  const ctx = store.getStore();
  if (ctx) Object.assign(ctx.extra, fields);
}

function emit(ctx: RunContext): RunEvent {
  const totals = ctx.calls.reduce(
    (t, c) => ({
      calls: t.calls + 1,
      inputTokens: t.inputTokens + c.inputTokens,
      outputTokens: t.outputTokens + c.outputTokens,
      cachedInputTokens: t.cachedInputTokens + c.cachedInputTokens,
      costUsd: t.costUsd + c.costUsd,
      durationMs: t.durationMs + c.durationMs,
    }),
    { calls: 0, inputTokens: 0, outputTokens: 0, cachedInputTokens: 0, costUsd: 0, durationMs: 0 },
  );

  const event: RunEvent = {
    ts: new Date().toISOString(),
    kind: ctx.kind,
    subject: ctx.subject,
    mode: ctx.mode,
    cached: ctx.extra.cached ?? false,
    calls: ctx.calls,
    totals,
    node: process.version,
  };
  if (ctx.extra.revised !== undefined) event.revised = ctx.extra.revised;

  try {
    mkdirSync(CACHE_DIR, { recursive: true });
    appendFileSync(USAGE_LOG, JSON.stringify(event) + "\n");
  } catch {
    // Never let logging break a run.
  }
  return event;
}

// Run `fn` inside a usage-tracking context; emit one wide event when it settles.
// Returns fn's result; the emitted event is available via the optional onEvent hook.
export async function withRun<T>(
  meta: { kind: RunEvent["kind"]; subject: string; mode?: string },
  fn: () => Promise<T>,
  onEvent?: (event: RunEvent) => void,
): Promise<T> {
  const ctx: RunContext = {
    kind: meta.kind,
    subject: meta.subject,
    mode: meta.mode ?? "thorough",
    calls: [],
    extra: {},
  };
  try {
    return await store.run(ctx, fn);
  } finally {
    onEvent?.(emit(ctx));
  }
}

export function formatEvent(e: RunEvent): string {
  const t = e.totals;
  const head = `[usage] ${e.kind} ${e.subject} · mode=${e.mode}${e.cached ? " · CACHE HIT (0 calls)" : ""}${
    e.revised ? " · revised" : ""
  }`;
  const lines = [head];
  for (const c of e.calls) {
    lines.push(
      `  ${c.callSite.padEnd(16)} ${c.model.padEnd(28)} in=${c.inputTokens} (cached ${c.cachedInputTokens}) out=${c.outputTokens} $${c.costUsd.toFixed(5)} ${c.durationMs}ms`,
    );
  }
  lines.push(
    `  TOTAL  calls=${t.calls} in=${t.inputTokens} (cached ${t.cachedInputTokens}) out=${t.outputTokens} $${t.costUsd.toFixed(5)} ${t.durationMs}ms`,
  );
  return lines.join("\n");
}

// --- `nr usage` rollup -------------------------------------------------------

export function readEvents(): RunEvent[] {
  let raw: string;
  try {
    raw = readFileSync(USAGE_LOG, "utf8");
  } catch {
    return [];
  }
  const events: RunEvent[] = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line) as RunEvent);
    } catch {
      // skip malformed line
    }
  }
  return events;
}

export function formatRollup(limit = 20): string {
  const events = readEvents();
  if (events.length === 0) return "No usage recorded yet. Run `nr research <SYMBOL>` first.";

  const recent = events.slice(-limit);
  const lines: string[] = [];
  lines.push(`USAGE — last ${recent.length} of ${events.length} runs`);
  lines.push("=".repeat(72));
  for (const e of recent) {
    const t = e.totals;
    lines.push(
      `${e.ts}  ${e.kind.padEnd(8)} ${e.subject.slice(0, 22).padEnd(22)} ${e.mode.padEnd(8)} ` +
        `calls=${t.calls} in=${t.inputTokens}(c${t.cachedInputTokens}) out=${t.outputTokens} $${t.costUsd.toFixed(5)}${e.cached ? " [cache]" : ""}`,
    );
  }
  lines.push("-".repeat(72));
  const sum = events.reduce(
    (s, e) => ({
      cost: s.cost + e.totals.costUsd,
      input: s.input + e.totals.inputTokens,
      cached: s.cached + e.totals.cachedInputTokens,
      output: s.output + e.totals.outputTokens,
    }),
    { cost: 0, input: 0, cached: 0, output: 0 },
  );
  lines.push(
    `ALL ${events.length} runs:  $${sum.cost.toFixed(4)}  in=${sum.input} (cached ${sum.cached}, ${
      sum.input ? ((sum.cached / sum.input) * 100).toFixed(1) : "0"
    }%)  out=${sum.output}`,
  );
  return lines.join("\n");
}
