import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Download, Play, Plus, Save, Trash2 } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ErrorBlock, Panel, SectionSkeleton, Spinner, TableSkeleton } from "@/components/common";
import {
  useBrokerSyncMutation,
  useOptimizeMutation,
  useTargetsMutation,
  useWhatIfMutation,
} from "@/api/mutations";
import {
  useCorrelationQuery,
  useHoldingsQuery,
  useMetaQuery,
  useNavQuery,
  usePerformanceQuery,
  usePortfolioSummaryQuery,
  useRebalanceQuery,
  useRegimeQuery,
  useTargetsQuery,
  useTaxQuery,
  useTransactionsQuery,
  useTwrQuery,
} from "@/api/queries";
import { fmtNum, fmtUsd, fmtUsd0, pct } from "@/lib/format";
import { optimizerWeightsToTargets } from "@/lib/optimizer";
import type { components } from "@/api/schema";

type HoldingValuation = components["schemas"]["HoldingValuation"];
type TargetRow = components["schemas"]["TargetRow"];

const PIE_COLORS = ["#22c55e", "#38bdf8", "#f59e0b", "#a78bfa", "#f43f5e", "#14b8a6", "#eab308"];

export const Route = createFileRoute("/portfolio")({
  component: PortfolioPage,
});

function PortfolioPage() {
  const [tab, setTab] = useState("overview");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-3xl font-semibold">Portfolio</h1>
        <p className="mt-2 text-muted-foreground">Holdings, transactions, targets, and analytics.</p>
      </div>
      <Tabs value={tab} onValueChange={(value) => setTab(value)}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="holdings">Holdings</TabsTrigger>
          <TabsTrigger value="transactions">Transactions</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
          <TabsTrigger value="analytics">Analytics</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">
          <OverviewTab />
        </TabsContent>
        <TabsContent value="holdings">
          <HoldingsTab />
        </TabsContent>
        <TabsContent value="transactions">
          <TransactionsTab />
        </TabsContent>
        <TabsContent value="plan">
          <PlanTab />
        </TabsContent>
        <TabsContent value="analytics">
          <AnalyticsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function OverviewTab() {
  const summary = usePortfolioSummaryQuery();
  const nav = useNavQuery();
  const regime = useRegimeQuery();
  const correlation = useCorrelationQuery();

  if (summary.error) return <ErrorBlock error={summary.error} onRetry={() => void summary.refetch()} />;
  const valuation = summary.data?.valuation;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-4">
        <Panel title="Total value" className="md:col-span-2">
          {summary.isLoading || !valuation ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-40" />
              <Skeleton className="h-4 w-36" />
            </div>
          ) : (
            <>
              <div className="font-serif text-4xl font-semibold">{fmtUsd(valuation.total_with_cash)}</div>
              <p className="mt-2 text-sm text-muted-foreground">Cash {fmtUsd(valuation.cash)} ({pct(valuation.cash_pct)})</p>
            </>
          )}
        </Panel>
        <Panel title="Unrealized P/L">
          {summary.isLoading || !valuation ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-28" />
              <Skeleton className="h-4 w-16" />
            </div>
          ) : (
            <>
              <div className="text-2xl font-semibold">{fmtUsd(valuation.total_unrealized_pl)}</div>
              <p className="text-sm text-muted-foreground">{pct(valuation.total_unrealized_pl_pct)}</p>
            </>
          )}
        </Panel>
        <Panel title="Health">
          {summary.isLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : summary.data?.health ? (
            <div className="space-y-1 text-sm">
              <Badge>{summary.data.health.concentration_level}</Badge>
              <div>{summary.data.health.diversification_note}</div>
              <div className="text-muted-foreground">
                Top holding {summary.data.health.top1_symbol ?? "n/a"} at {pct(summary.data.health.top1_pct)}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No holdings yet.</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Allocation">
          {summary.isLoading ? (
            <Skeleton className="h-72 w-full" />
          ) : summary.data ? (
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={summary.data.allocation} dataKey="value" nameKey="label" innerRadius={55} outerRadius={95}>
                    {summary.data.allocation.map((slice, index) => (
                      <Cell key={slice.label} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => fmtUsd(Number(value))} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : null}
        </Panel>

        <Panel title="NAV">
          {nav.isLoading ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-4">
                <Skeleton className="h-8 w-16" />
                <Skeleton className="h-8 w-16" />
                <Skeleton className="h-8 w-16" />
              </div>
              <Skeleton className="h-56 w-full" />
            </div>
          ) : null}
          {nav.error ? <ErrorBlock error={nav.error} onRetry={() => void nav.refetch()} /> : null}
          {nav.data ? (
            <>
              <div className="mb-3 flex flex-wrap gap-4 text-sm">
                <Metric label="1D" value={nav.data.series.change_1d_pct == null ? "n/a" : pct(nav.data.series.change_1d_pct)} />
                <Metric label="Total" value={nav.data.series.change_total_pct == null ? "n/a" : pct(nav.data.series.change_total_pct)} />
                <Metric label="MWR" value={nav.data.returns.mwr_annualized == null ? "n/a" : pct(nav.data.returns.mwr_annualized)} />
              </div>
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={nav.data.series.points}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                    <XAxis dataKey="day" />
                    <YAxis tickFormatter={(value) => fmtUsd0(Number(value))} />
                    <Tooltip formatter={(value) => fmtUsd(Number(value))} />
                    <Area dataKey="total_with_cash" stroke="#22c55e" fill="#22c55e" fillOpacity={0.2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : null}
        </Panel>
      </div>

      {summary.data ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Regime signal">
            {regime.isLoading ? <SectionSkeleton rows={3} /> : null}
            {regime.error ? <ErrorBlock error={regime.error} onRetry={() => void regime.refetch()} /> : null}
            {regime.data?.signal ? (
              <div className="space-y-2 text-sm">
                <Badge>{regime.data.signal.level}</Badge>
                <p>{regime.data.signal.note}</p>
                <p className="text-muted-foreground">
                  Short vol {pct(regime.data.signal.short_vol)}, long vol {pct(regime.data.signal.long_vol)}, ratio{" "}
                  {fmtNum(regime.data.signal.vol_ratio)}
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Add holdings to compute a regime signal.</p>
            )}
          </Panel>
          <Panel title="Correlation">
            {correlation.isLoading ? <SectionSkeleton rows={4} /> : null}
            {correlation.error ? <ErrorBlock error={correlation.error} onRetry={() => void correlation.refetch()} /> : null}
            {correlation.data ? <CorrelationMatrix data={correlation.data} /> : null}
          </Panel>
        </div>
      ) : null}

      {summary.data ? <p className="text-xs text-muted-foreground">{summary.data.disclaimer}</p> : null}
    </div>
  );
}

function HoldingsTab() {
  const meta = useMetaQuery();
  const holdings = useHoldingsQuery();
  const brokerSync = useBrokerSyncMutation();
  const lastSync = brokerSync.data?.last_sync ?? meta.data?.last_broker_sync ?? "n/a";

  if (holdings.error) return <ErrorBlock error={holdings.error} onRetry={() => void holdings.refetch()} />;
  const valuation = holdings.data?.valuation;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="Total value">
          {holdings.isLoading || !valuation ? (
            <Skeleton className="h-8 w-28" />
          ) : (
            <div className="text-2xl font-semibold">{fmtUsd(valuation.total_with_cash)}</div>
          )}
        </Panel>
        <Panel title="Cash">
          {holdings.isLoading || !valuation ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-28" />
              <Skeleton className="h-4 w-16" />
            </div>
          ) : (
            <>
              <div className="text-2xl font-semibold">{fmtUsd(valuation.cash)}</div>
              <p className="text-sm text-muted-foreground">{pct(valuation.cash_pct)}</p>
            </>
          )}
        </Panel>
        <Panel title="Broker sync">
          <div className="space-y-3">
            {meta.data?.broker_sync_configured ? (
              <Button variant="outline" disabled={brokerSync.isPending} onClick={() => brokerSync.mutate()}>
                {brokerSync.isPending ? <Spinner /> : <Download />}
                Sync
              </Button>
            ) : meta.isLoading ? (
              <Skeleton className="h-9 w-24" />
            ) : (
              <p className="text-sm text-muted-foreground">Not configured.</p>
            )}
            <div className="text-sm text-muted-foreground">
              Last sync {lastSync}
              {brokerSync.data ? `; imported ${fmtNum(brokerSync.data.result.imported_activities)} activities` : ""}
            </div>
          </div>
        </Panel>
      </div>
      <Panel title="Holdings">
        {holdings.isLoading || !holdings.data ? (
          <TableSkeleton headers={["Symbol", "Shares", "Price", "Market value", "Weight", "Avg cost", "P/L"]} />
        ) : (
          <HoldingsTable holdings={holdings.data.valuation.holdings} />
        )}
      </Panel>
    </div>
  );
}

const LEDGER_HEADERS = ["Date", "Side", "Symbol", "Shares", "Price", "Amount", "Realized P/L", "Note"];

function TransactionsTab() {
  const transactions = useTransactionsQuery();

  if (transactions.error) return <ErrorBlock error={transactions.error} onRetry={() => void transactions.refetch()} />;
  const returns = transactions.data?.returns;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-4">
        <Panel title="Deposits">
          {transactions.isLoading || !returns ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-semibold">{fmtUsd(returns.total_deposited)}</div>
          )}
        </Panel>
        <Panel title="Withdrawals">
          {transactions.isLoading || !returns ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-semibold">{fmtUsd(returns.total_withdrawn)}</div>
          )}
        </Panel>
        <Panel title="Dividends">
          {transactions.isLoading || !returns ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-semibold">{fmtUsd(returns.dividends_total)}</div>
          )}
        </Panel>
        <Panel title="Realized P/L">
          {transactions.isLoading || !returns ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <div className="text-xl font-semibold">{fmtUsd(returns.realized_pl_total)}</div>
          )}
        </Panel>
      </div>

      <Panel title="Ledger">
        {transactions.isLoading || !transactions.data ? (
          <TableSkeleton headers={LEDGER_HEADERS} />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {LEDGER_HEADERS.map((header) => (
                    <TableHead key={header}>{header}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {transactions.data.transactions.map((txn) => (
                  <TableRow key={txn.id ?? `${txn.ts}-${txn.side}-${txn.amount}`}>
                    <TableCell>{txn.ts}</TableCell>
                    <TableCell>{txn.side}</TableCell>
                    <TableCell>{txn.symbol ?? "n/a"}</TableCell>
                    <TableCell>{fmtNum(txn.shares)}</TableCell>
                    <TableCell>{fmtUsd(txn.price)}</TableCell>
                    <TableCell>{fmtUsd(txn.amount)}</TableCell>
                    <TableCell>{fmtUsd(txn.realized_pl)}</TableCell>
                    <TableCell>{txn.note}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function PlanTab() {
  const targets = useTargetsQuery();
  const rebalance = useRebalanceQuery();
  const updateTargets = useTargetsMutation();
  const whatif = useWhatIfMutation();
  const [rows, setRows] = useState<TargetRow[]>([]);
  const [amount, setAmount] = useState("");

  useEffect(() => {
    if (targets.data) setRows(targets.data.rows);
  }, [targets.data]);

  function saveTargets() {
    updateTargets.mutate({
      targets: rows
        .filter((row) => row.symbol.trim() && row.weight_pct !== null)
        .map((row) => ({ symbol: row.symbol.toUpperCase(), weight_pct: Number(row.weight_pct) })),
    });
  }

  return (
    <div className="space-y-5">
      {targets.isLoading ? <SectionSkeleton rows={5} /> : null}
      {targets.error ? <ErrorBlock error={targets.error} onRetry={() => void targets.refetch()} /> : null}
      {targets.data ? (
        <Panel title="Target editor">
          <div className="space-y-2">
            {rows.length > 0 ? (
              <div className="grid grid-cols-[1fr_1fr_auto] gap-2 text-xs text-muted-foreground">
                <span>Symbol</span>
                <span>Target weight %</span>
                <span />
              </div>
            ) : null}
            {rows.map((row, index) => (
              <div key={index} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                <Input
                  value={row.symbol}
                  placeholder="Symbol"
                  onChange={(event) => setRows(rows.map((r, i) => (i === index ? { ...r, symbol: event.target.value } : r)))}
                />
                <Input
                  value={row.weight_pct ?? ""}
                  placeholder="Weight %"
                  type="number"
                  step="any"
                  onChange={(event) =>
                    setRows(rows.map((r, i) => (i === index ? { ...r, weight_pct: event.target.value ? Number(event.target.value) : null } : r)))
                  }
                />
                <Button size="icon" variant="ghost" onClick={() => setRows(rows.filter((_, i) => i !== index))}>
                  <Trash2 />
                </Button>
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={() => setRows([...rows, { symbol: "", weight_pct: null }])}>
                <Plus />
                Add row
              </Button>
              <Button disabled={updateTargets.isPending} onClick={saveTargets}>
                {updateTargets.isPending ? <Spinner /> : <Save />}
                Save targets
              </Button>
              <span className="text-sm text-muted-foreground">Implicit cash {pct(targets.data.implicit_cash_weight)}</span>
            </div>
          </div>
        </Panel>
      ) : null}

      <Panel title="Rebalance plan">
        {rebalance.isLoading ? <SectionSkeleton rows={4} /> : null}
        {rebalance.error ? <ErrorBlock error={rebalance.error} onRetry={() => void rebalance.refetch()} /> : null}
        {rebalance.data?.plan ? <RebalanceTable items={rebalance.data.plan.items} /> : <p className="text-sm text-muted-foreground">No targets set.</p>}
      </Panel>

      <Panel title="What-if contribution">
        <div className="mb-3 flex gap-2">
          <Input value={amount} type="number" step="any" placeholder="Amount" onChange={(event) => setAmount(event.target.value)} />
          <Button disabled={whatif.isPending || !amount} onClick={() => whatif.mutate({ amount: Number(amount) })}>
            {whatif.isPending ? <Spinner /> : <Play />}
            Run
          </Button>
        </div>
        {whatif.data?.plan ? <ContributionTable items={whatif.data.plan.items} /> : null}
      </Panel>
    </div>
  );
}

function AnalyticsTab() {
  const summary = usePortfolioSummaryQuery();
  const performance = usePerformanceQuery();
  const twr = useTwrQuery();
  const tax = useTaxQuery();
  const optimize = useOptimizeMutation();
  const updateTargets = useTargetsMutation();
  const [objective, setObjective] = useState<"max_sharpe" | "min_risk">("max_sharpe");
  const [showTearsheet, setShowTearsheet] = useState(false);

  const seed = summary.data?.optimizer_seed ?? [];
  const objectiveLabel = objective === "max_sharpe" ? "Max Sharpe" : "Min risk";

  function runOptimizer() {
    optimize.mutate({
      objective,
      holdings: seed.map((row) => ({
        symbol: row.symbol,
        value: row.value,
        price: row.price,
      })),
    });
  }

  function adoptTargets() {
    if (!optimize.data?.result) return;
    updateTargets.mutate({ targets: optimizerWeightsToTargets(optimize.data.result) });
  }

  return (
    <div className="space-y-5">
      <Panel title="Performance">
        {performance.isLoading ? <SectionSkeleton rows={4} /> : null}
        {performance.error ? <ErrorBlock error={performance.error} onRetry={() => void performance.refetch()} /> : null}
        {performance.data?.metrics ? (
          <div className="grid gap-3 md:grid-cols-5">
            <Metric label="CAGR" value={pct(performance.data.metrics.cagr)} />
            <Metric label="Total return" value={pct(performance.data.metrics.total_return)} />
            <Metric label="Sharpe" value={fmtNum(performance.data.metrics.sharpe)} />
            <Metric label="Volatility" value={pct(performance.data.metrics.volatility)} />
            <Metric label="Max drawdown" value={pct(performance.data.metrics.max_drawdown)} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Add holdings to compute performance.</p>
        )}
        {performance.data ? <p className="mt-3 text-xs text-muted-foreground">{performance.data.backtest_caveat}</p> : null}
      </Panel>

      <Panel title="Time-weighted return">
        {twr.isLoading ? <SectionSkeleton rows={2} /> : null}
        {twr.error ? <ErrorBlock error={twr.error} onRetry={() => void twr.refetch()} /> : null}
        {twr.data ? (
          <div className="grid gap-3 md:grid-cols-4">
            <Metric label="Cumulative" value={pct(twr.data.twr_cumulative)} />
            <Metric label="Annualized" value={twr.data.twr_annualized == null ? "n/a" : pct(twr.data.twr_annualized)} />
            <Metric label="Benchmark" value={twr.data.benchmark} />
            <Metric label="Excess" value={twr.data.excess_cumulative == null ? "n/a" : pct(twr.data.excess_cumulative)} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No TWR summary available.</p>
        )}
      </Panel>

      <Panel title="Tax signals">
        {tax.isLoading ? <SectionSkeleton rows={4} /> : null}
        {tax.error ? <ErrorBlock error={tax.error} onRetry={() => void tax.refetch()} /> : null}
        {tax.data ? (
          <div className="space-y-4">
            <SimpleTable
              headers={["Symbol", "Unrealized P/L", "Unrealized %", "Wash risk", "Note"]}
              rows={tax.data.harvest_candidates.map((item) => [
                item.symbol,
                fmtUsd(item.unrealized_pl),
                pct(item.unrealized_pct),
                item.wash_sale_risk ? "yes" : "no",
                item.note,
              ])}
            />
            <SimpleTable
              headers={["Symbol", "Loss sale", "Repurchase", "Realized P/L", "Note"]}
              rows={tax.data.repurchase_flags.map((item) => [
                item.symbol,
                item.loss_sale_date,
                item.repurchase_date,
                fmtUsd(item.realized_pl),
                item.note,
              ])}
            />
            <p className="text-xs text-muted-foreground">{tax.data.disclaimer}</p>
          </div>
        ) : null}
      </Panel>

      <Panel title="Optimizer">
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">Optimizes over your current holdings.</p>
          {summary.isLoading ? (
            <TableSkeleton headers={["Symbol", "Value", "Price"]} />
          ) : seed.length === 0 ? (
            <p className="text-sm text-muted-foreground">No holdings to optimize.</p>
          ) : (
            <SimpleTable
              headers={["Symbol", "Value", "Price"]}
              rows={seed.map((row) => [row.symbol, fmtUsd(row.value), fmtUsd(row.price)])}
            />
          )}
          <div className="flex flex-wrap gap-2">
            <Select value={objective} onValueChange={(value) => setObjective(value as "max_sharpe" | "min_risk")}>
              <SelectTrigger>
                <SelectValue>{objectiveLabel}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="max_sharpe">Max Sharpe</SelectItem>
                <SelectItem value="min_risk">Min risk</SelectItem>
              </SelectContent>
            </Select>
            <Button disabled={optimize.isPending || seed.length === 0} onClick={runOptimizer}>
              {optimize.isPending ? <Spinner /> : <Play />}
              Optimize
            </Button>
          </div>

          {optimize.data ? <OptimizerResult data={optimize.data} onAdopt={adoptTargets} adopting={updateTargets.isPending} /> : null}
        </div>
      </Panel>

      <Panel title="Tearsheet">
        <Button variant="outline" onClick={() => setShowTearsheet(true)}>
          <Download />
          Load tearsheet
        </Button>
        {showTearsheet ? <iframe title="Portfolio tearsheet" src="/api/portfolio/tearsheet" className="mt-3 h-[720px] w-full rounded-md border border-border" /> : null}
      </Panel>
    </div>
  );
}

function HoldingsTable({ holdings }: { holdings: HoldingValuation[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Shares</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Market value</TableHead>
            <TableHead>Weight</TableHead>
            <TableHead>Avg cost</TableHead>
            <TableHead>P/L</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {holdings.map((holding) => (
            <TableRow key={holding.symbol}>
              <TableCell>
                <Link to="/research/$symbol" params={{ symbol: holding.symbol }} className="font-medium hover:underline">
                  {holding.symbol}
                </Link>
              </TableCell>
              <TableCell>{fmtNum(holding.shares)}</TableCell>
              <TableCell>{fmtUsd(holding.price)}</TableCell>
              <TableCell>{fmtUsd(holding.market_value)}</TableCell>
              <TableCell>{holding.weight == null ? "n/a" : pct(holding.weight)}</TableCell>
              <TableCell>{fmtUsd(holding.avg_cost)}</TableCell>
              <TableCell>
                {fmtUsd(holding.unrealized_pl)} {holding.unrealized_pl_pct == null ? "" : `(${pct(holding.unrealized_pl_pct)})`}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function CorrelationMatrix({ data }: { data: components["schemas"]["CorrelationResponse"] }) {
  if (data.too_few || !data.insight?.matrix) return <p className="text-sm text-muted-foreground">n/a</p>;
  const symbols = data.insight.symbols;
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead></TableHead>
            {symbols.map((symbol) => (
              <TableHead key={symbol}>{symbol}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {symbols.map((row) => (
            <TableRow key={row}>
              <TableCell className="font-medium">{row}</TableCell>
              {symbols.map((column) => {
                const value = data.insight?.matrix?.[row]?.[column];
                return (
                  <TableCell key={column} style={{ backgroundColor: value == null ? undefined : correlationColor(value) }}>
                    {value == null ? "n/a" : fmtNum(value)}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="mt-2 text-sm text-muted-foreground">{data.insight.note}</p>
    </div>
  );
}

function correlationColor(value: number): string {
  const clamped = Math.max(-1, Math.min(1, value));
  const hue = 120 - ((clamped + 1) / 2) * 120;
  return `hsl(${hue} 60% 32% / 0.5)`;
}

function RebalanceTable({ items }: { items: components["schemas"]["RebalanceItem"][] }) {
  return (
    <SimpleTable
      headers={["Symbol", "Current", "Target", "Drift", "Action", "Delta", "Shares"]}
      rows={items.map((item) => [
        item.symbol,
        pct(item.current_weight),
        pct(item.target_weight),
        pct(item.drift),
        item.action,
        fmtUsd(item.delta_usd),
        fmtNum(item.delta_shares),
      ])}
    />
  );
}

function ContributionTable({ items }: { items: components["schemas"]["ContributionItem"][] }) {
  return (
    <SimpleTable
      headers={["Symbol", "Current", "Target", "Buy", "Shares", "After"]}
      rows={items.map((item) => [
        item.symbol,
        pct(item.current_weight),
        pct(item.target_weight),
        fmtUsd(item.buy_usd),
        fmtNum(item.buy_shares),
        pct(item.after_weight),
      ])}
    />
  );
}

function OptimizerResult({
  data,
  onAdopt,
  adopting,
}: {
  data: components["schemas"]["OptimizeResponse"];
  onAdopt: () => void;
  adopting: boolean;
}) {
  if (!data.available || !data.result) {
    return <div className="rounded-md border border-border p-3 text-sm text-muted-foreground">{data.reason ?? "Optimizer unavailable."}</div>;
  }
  const frontier = data.result.efficient_frontier ?? [];
  const optimalPoint = [{ ...data.result.optimal, name: "optimal" }];
  const currentPoint = data.result.current ? [{ ...data.result.current, name: "current" }] : [];
  return (
    <div className="space-y-4">
      {data.warnings?.length ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          {data.warnings.map((warning) => (
            <div key={warning}>{warning}</div>
          ))}
        </div>
      ) : null}
      <SimpleTable
        headers={["Symbol", "Weight"]}
        rows={Object.entries(data.result.optimal.weights).map(([symbol, weight]) => [symbol, pct(weight)])}
      />
      <div className="grid gap-3 md:grid-cols-3">
        <Metric label="Expected return" value={pct(data.result.optimal.expected_return)} />
        <Metric label="Volatility" value={pct(data.result.optimal.volatility)} />
        <Metric label="Sharpe" value={fmtNum(data.result.optimal.sharpe)} />
      </div>
      {data.drift?.items.length ? (
        <SimpleTable
          headers={["Symbol", "Current", "Target", "Drift", "Suggestion"]}
          rows={data.drift.items.map((item) => [item.symbol, pct(item.current_weight), pct(item.target_weight), pct(item.drift), item.suggestion])}
        />
      ) : null}
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis dataKey="volatility" name="Volatility" tickFormatter={(value) => pct(Number(value))} />
            <YAxis dataKey="expected_return" name="Return" tickFormatter={(value) => pct(Number(value))} />
            <Tooltip formatter={(value) => (typeof value === "number" ? pct(value) : String(value))} />
            <Scatter data={frontier} fill="#38bdf8" />
            <Scatter data={optimalPoint} fill="#22c55e" />
            <Scatter data={currentPoint} fill="#f59e0b" />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <Button disabled={adopting} onClick={onAdopt}>
        {adopting ? <Spinner /> : <Save />}
        Adopt as targets
      </Button>
      <p className="text-xs text-muted-foreground">{data.result.disclaimer}</p>
    </div>
  );
}

function SimpleTable({ headers, rows }: { headers: string[]; rows: string[][] }) {
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
          {rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={headers.length} className="text-muted-foreground">
                No rows.
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row, index) => (
              <TableRow key={index}>
                {row.map((cell, cellIndex) => (
                  <TableCell key={cellIndex}>{cell}</TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
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
