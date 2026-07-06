import { RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, apiErrorMessage } from "@/api/errors";
import { cn } from "@/lib/utils";

export function ErrorBlock({
  error,
  onRetry,
  title = "Request failed",
  prominent = false,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  prominent?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm",
        prominent && "border-destructive bg-destructive/20 text-base",
      )}
    >
      <div className="font-medium">{title}</div>
      <p className="mt-1 text-muted-foreground">{apiErrorMessage(error)}</p>
      {error instanceof ApiError ? (
        <p className="mt-1 font-mono text-xs text-muted-foreground">
          {error.request} &rarr; {error.status === 0 ? "network error (backend unreachable?)" : `HTTP ${error.status}`}
        </p>
      ) : null}
      {onRetry ? (
        <Button className="mt-3" size="sm" variant="outline" onClick={onRetry}>
          <RotateCcw />
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function SectionSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function TableSkeleton({ headers, rows = 5 }: { headers: string[]; rows?: number }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            {headers.map((header) => (
              <TableHead key={header}>{header}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: rows }).map((_, rowIndex) => (
            <TableRow key={rowIndex}>
              {headers.map((_, cellIndex) => (
                <TableCell key={cellIndex}>
                  <Skeleton className="h-4 w-full" />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <span className={cn("size-3 animate-spin rounded-full border border-current border-t-transparent", className)} />;
}

export function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-lg border border-border bg-card p-4", className)}>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      {children}
    </section>
  );
}
