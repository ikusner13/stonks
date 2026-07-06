import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Bookmark, BookmarkCheck, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ErrorBlock, Panel, SectionSkeleton } from "@/components/common";
import { ApiError } from "@/api/errors";
import { useWatchMutation } from "@/api/mutations";
import { useResearchQuery } from "@/api/queries";
import { fmtCap, fmtIndicatorValue, fmtNum, pct } from "@/lib/format";
import type { components } from "@/api/schema";

type Confidence = components["schemas"]["ResearchResponse"]["effective_confidence"];
type Indicator = components["schemas"]["Indicator"];

export const Route = createFileRoute("/research/$symbol")({
  component: ResearchPage,
});

function ResearchPage() {
  const { symbol } = Route.useParams();
  const upper = symbol.toUpperCase();
  const [mode, setMode] = useState<"thorough" | "cheap">("thorough");
  const [profile, setProfile] = useState<"none" | "penny" | "largecap">("none");
  const [fresh, setFresh] = useState(0);
  const research = useResearchQuery(upper, mode, profile, fresh);
  const watch = useWatchMutation();
  const isBudget = research.error instanceof ApiError && research.error.status === 429;
  const isInsufficient = research.error instanceof ApiError && research.error.status === 404;
  const data = research.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">{upper}</h1>
          <p className="mt-2 text-muted-foreground">Research report and deterministic indicators.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant={mode === "thorough" ? "default" : "outline"} onClick={() => setMode("thorough")}>
            Thorough
          </Button>
          <Button variant={mode === "cheap" ? "default" : "outline"} onClick={() => setMode("cheap")}>
            Cheap
          </Button>
          <Select value={profile} onValueChange={(value) => setProfile(value as "none" | "penny" | "largecap")}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No override</SelectItem>
              <SelectItem value="penny">Penny</SelectItem>
              <SelectItem value="largecap">Large cap</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" disabled={research.isFetching} onClick={() => setFresh((value) => value + 1)}>
            <RefreshCw />
            Refresh
          </Button>
        </div>
      </div>

      {research.isLoading || research.isFetching ? (
        <div className="rounded-lg border border-border p-4">
          <div className="mb-3 text-sm text-muted-foreground">Researching... this can take a minute.</div>
          <SectionSkeleton rows={8} />
        </div>
      ) : null}

      {research.error ? (
        <ErrorBlock
          error={research.error}
          prominent={isBudget || isInsufficient}
          title={isBudget ? "Daily budget reached" : isInsufficient ? "Ticker not found" : "Research failed"}
          onRetry={isBudget ? undefined : () => void research.refetch()}
        />
      ) : null}

      {data ? (
        <div className="space-y-5">
          <Panel title="Report">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold">{data.result.report.company_name}</h2>
              <ConfidenceBadge confidence={data.effective_confidence} />
              <Badge variant="outline">{data.profile_label}</Badge>
              <Button
                size="sm"
                variant="outline"
                disabled={watch.isPending}
                onClick={() => watch.mutate({ symbol: upper, watched: data.watched })}
              >
                {data.watched ? <BookmarkCheck /> : <Bookmark />}
                {data.watched ? "Watching" : "Watch"}
              </Button>
            </div>
            <p className="mt-3 text-sm leading-6">{data.result.report.summary}</p>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <TextList title="Bull case" items={data.result.report.thesis.bull} />
              <TextList title="Bear case" items={data.result.report.thesis.bear} />
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <TextList title="Risks" items={data.result.report.risks} />
              <TextList title="Things to investigate" items={data.result.report.things_to_investigate} />
            </div>
            <div className="mt-4 space-y-3">
              <p className="text-sm">
                <span className="font-medium">Valuation:</span> {data.result.report.valuation_context}
              </p>
              <p className="text-sm">
                <span className="font-medium">Indicator view:</span> {data.result.report.indicator_view}
              </p>
            </div>
          </Panel>

          <Panel title="Key metrics">
            <div className="grid gap-3 md:grid-cols-2">
              {data.result.report.key_metrics.map((metric) => (
                <div key={`${metric.label}-${metric.value}`} className="rounded-md border border-border p-3">
                  <div className="font-medium">{metric.label}</div>
                  <div className="text-lg">{metric.value}</div>
                  <p className="mt-1 text-sm text-muted-foreground">{metric.interpretation}</p>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Ticker data">
            <div className="grid gap-3 text-sm md:grid-cols-4">
              <Metric label="Price" value={fmtCap(data.result.ticker.quote?.price ?? null)} />
              <Metric label="Change" value={fmtNum(data.result.ticker.quote?.change ?? null)} />
              <Metric label="Market cap" value={fmtCap(data.result.ticker.fundamentals?.market_cap ?? null)} />
              <Metric label="P/E" value={fmtNum(data.result.ticker.fundamentals?.pe_ratio ?? null)} />
            </div>
          </Panel>

          <Panel title="Indicator scorecard">
            {data.result.scorecard ? (
              <IndicatorTable indicators={data.result.scorecard.indicators} />
            ) : (
              <p className="text-sm text-muted-foreground">No scorecard available.</p>
            )}
          </Panel>

          <Panel title="Sizing guidance">
            <div className="grid gap-3 md:grid-cols-3">
              <Metric label="Suggested band" value={`${pct(data.sizing.low_pct)} - ${pct(data.sizing.high_pct)}`} />
              <Metric label="Dollar band" value={`${fmtCap(data.sizing.low_dollars)} - ${fmtCap(data.sizing.high_dollars)}`} />
              <Metric label="Current weight" value={data.sizing.current_weight == null ? "n/a" : pct(data.sizing.current_weight)} />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{data.sizing.note}</p>
          </Panel>

          <Panel title="Critique">
            <p className="text-sm">{data.result.critique.overall_assessment}</p>
            <p className="mt-2 text-sm text-muted-foreground">{data.result.critique.fabrication_check.details}</p>
            {data.result.critique.issues.length ? (
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
                {data.result.critique.issues.map((issue) => (
                  <li key={`${issue.field}-${issue.problem}`}>
                    <span className="font-medium">{issue.severity}</span> {issue.field}: {issue.problem} Fix: {issue.fix}
                  </li>
                ))}
              </ul>
            ) : null}
          </Panel>
        </div>
      ) : null}
    </div>
  );
}

function IndicatorTable({ indicators }: { indicators: Indicator[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Key</TableHead>
          <TableHead>Value</TableHead>
          <TableHead>Signal</TableHead>
          <TableHead>Weight</TableHead>
          <TableHead>Detail</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {indicators.map((indicator) => (
          <TableRow key={indicator.key}>
            <TableCell>
              <div className="font-medium">{indicator.label}</div>
              <div className="text-xs text-muted-foreground">{indicator.key}</div>
            </TableCell>
            <TableCell>{fmtIndicatorValue(indicator.value, indicator.unit)}</TableCell>
            <TableCell>
              <SignalBadge signal={indicator.signal} />
            </TableCell>
            <TableCell>n/a</TableCell>
            <TableCell className="max-w-sm text-muted-foreground">{indicator.detail}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function SignalBadge({ signal }: { signal: Indicator["signal"] }) {
  if (signal === "bullish") return <Badge className="bg-emerald-600 text-white">bullish</Badge>;
  if (signal === "bearish") return <Badge variant="destructive">bearish</Badge>;
  if (signal === "unavailable") return <Badge variant="outline">unavailable</Badge>;
  return <Badge variant="secondary">neutral</Badge>;
}

function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const variant = confidence === "high" ? "default" : confidence === "medium" ? "secondary" : "destructive";
  return <Badge variant={variant}>{confidence} confidence</Badge>;
}

function TextList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="mb-2 font-medium">{title}</h3>
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 font-medium">{value}</div>
    </div>
  );
}
