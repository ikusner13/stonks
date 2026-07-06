import { useState, type FormEvent } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Bookmark, BookmarkCheck, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ErrorBlock, SectionSkeleton, Spinner } from "@/components/common";
import { useMetaQuery } from "@/api/queries";
import { useDiscoverMutation, useWatchMutation } from "@/api/mutations";
import { ApiError } from "@/api/errors";
import { fmtCap, fmtNum } from "@/lib/format";
import type { components } from "@/api/schema";

type Candidate = components["schemas"]["Candidate"];

export const Route = createFileRoute("/")({
  component: DiscoverPage,
});

function DiscoverPage() {
  const meta = useMetaQuery();
  const discover = useDiscoverMutation();
  const watch = useWatchMutation();
  const [goal, setGoal] = useState("");
  const [watched, setWatched] = useState<Set<string>>(new Set());

  const result = discover.data?.result;
  const isBudgetError = discover.error instanceof ApiError && discover.error.status === 429;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await discover.mutateAsync({ goal });
    setWatched(new Set(response.watched_symbols.map((symbol) => symbol.toUpperCase())));
  }

  async function toggle(symbol: string) {
    const upper = symbol.toUpperCase();
    const current = watched.has(upper);
    const response = await watch.mutateAsync({ symbol: upper, watched: current });
    setWatched((previous) => {
      const next = new Set(previous);
      if (response.watched) next.add(upper);
      else next.delete(upper);
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl font-semibold">Discover</h1>
        <p className="mt-2 text-muted-foreground">Screen for ideas, then send candidates into research.</p>
      </div>

      {meta.isLoading ? <SectionSkeleton rows={1} /> : null}
      {meta.error ? <ErrorBlock error={meta.error} onRetry={() => void meta.refetch()} /> : null}
      {meta.data && !meta.data.llm_configured ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-100">
          LLM discovery is not configured. Set the backend OpenRouter key before running searches.
        </div>
      ) : null}

      <form className="space-y-3" onSubmit={submit}>
        <Textarea
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          placeholder="AI infrastructure under $100B market cap"
          rows={4}
        />
        <div className="flex flex-wrap gap-2">
          {meta.data?.examples.map((example) => (
            <Button key={example} type="button" size="sm" variant="outline" onClick={() => setGoal(example)}>
              {example}
            </Button>
          ))}
        </div>
        <Button type="submit" disabled={discover.isPending || !goal.trim()}>
          {discover.isPending ? <Spinner /> : <Search />}
          Discover
        </Button>
      </form>

      {discover.isPending ? (
        <div className="rounded-lg border border-border p-4">
          <div className="mb-3 text-sm text-muted-foreground">Searching for candidates...</div>
          <SectionSkeleton rows={5} />
        </div>
      ) : null}
      {discover.error ? (
        <ErrorBlock
          error={discover.error}
          prominent={isBudgetError}
          title={isBudgetError ? "Daily budget reached" : "Discovery failed"}
        />
      ) : null}

      {result ? (
        <section className="space-y-3">
          <div>
            <h2 className="text-xl font-semibold">Results</h2>
            <p className="text-sm text-muted-foreground">{result.interpretation}</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {result.candidates.map((candidate) => (
              <CandidateCard
                key={candidate.symbol}
                candidate={candidate}
                watched={watched.has(candidate.symbol.toUpperCase())}
                pending={watch.isPending}
                onToggle={() => void toggle(candidate.symbol)}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function CandidateCard({
  candidate,
  watched,
  pending,
  onToggle,
}: {
  candidate: Candidate;
  watched: boolean;
  pending: boolean;
  onToggle: () => void;
}) {
  return (
    <article className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Link to="/research/$symbol" params={{ symbol: candidate.symbol }} className="text-lg font-semibold hover:underline">
            {candidate.symbol}
          </Link>
          <div className="text-sm text-muted-foreground">{candidate.name}</div>
        </div>
        <Button type="button" size="sm" variant="outline" disabled={pending} onClick={onToggle}>
          {watched ? <BookmarkCheck /> : <Bookmark />}
          {watched ? "Watching" : "Watch"}
        </Button>
      </div>
      <p className="mt-3 text-sm">{candidate.rationale}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <Badge variant="secondary">{candidate.source}</Badge>
        <Badge variant="outline">Cap {fmtCap(candidate.market_cap ?? null)}</Badge>
        <Badge variant="outline">P/E {fmtNum(candidate.pe_ratio ?? null)}</Badge>
      </div>
    </article>
  );
}
