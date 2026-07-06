import { Link, createFileRoute } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ErrorBlock, SectionSkeleton } from "@/components/common";
import { useWatchMutation } from "@/api/mutations";
import { useWatchlistQuery } from "@/api/queries";
import { fmtNum } from "@/lib/format";

export const Route = createFileRoute("/watchlist")({
  component: WatchlistPage,
});

function WatchlistPage() {
  const watchlist = useWatchlistQuery();
  const watch = useWatchMutation();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl font-semibold">Watchlist</h1>
        <p className="mt-2 text-muted-foreground">Saved symbols for follow-up research.</p>
      </div>

      {watchlist.isLoading ? <SectionSkeleton rows={6} /> : null}
      {watchlist.error ? <ErrorBlock error={watchlist.error} onRetry={() => void watchlist.refetch()} /> : null}
      {watchlist.data ? (
        <div className="rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Value</TableHead>
                <TableHead className="w-20"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {watchlist.data.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-muted-foreground">
                    No watchlist items yet.
                  </TableCell>
                </TableRow>
              ) : (
                watchlist.data.items.map((item) => (
                  <TableRow key={item.symbol}>
                    <TableCell>
                      <Link to="/research/$symbol" params={{ symbol: item.symbol }} className="font-medium hover:underline">
                        {item.symbol}
                      </Link>
                    </TableCell>
                    <TableCell>{fmtNum(item.value ?? null)}</TableCell>
                    <TableCell>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        disabled={watch.isPending}
                        onClick={() => watch.mutate({ symbol: item.symbol, watched: true })}
                      >
                        <Trash2 />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
