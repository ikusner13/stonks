"""CLI: `stocks research <SYMBOL>`, `stocks discover "<goal>"`, `stocks usage`.

Mirrors the original tsx CLI. The report goes to stdout; usage/progress to stderr.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

import typer
from rich.console import Console
from rich.table import Table

from . import db, ledger as ledger_mod
from .config import CACHE_DIR
from .llm.critic import ReviewMode
from .llm.discovery import discover_ideas
from .llm.pipeline import research_ticker_cached
from .llm.usage import format_event, format_rollup, with_run
from .schemas import Critique, DiscoveryResult, ResearchResult, TickerData, TickerReport

app = typer.Typer(add_completion=False, help="LLM-driven equity research assistant.")
console = Console()
CACHE_REPORT_RE = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})_(thorough|cheap)\.json$")


def _fmt_num(n: float | None) -> str:
    return "n/a" if n is None else f"{n:,.2f}".rstrip("0").rstrip(".")


def _fmt_cap(n: float | None) -> str:
    if n is None:
        return "n/a"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if n >= div:
            return f"${n / div:.2f}{suf}"
    return f"${_fmt_num(n)}"


def _fmt_pct(n: float | None) -> str:
    return "n/a" if n is None else f"{n * 100:.1f}%"


def _call_count() -> int:
    db.init_db()
    with db._conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM calls").fetchone()[0])


def _print_report(data: TickerData, report: TickerReport) -> None:
    lines: list[str] = ["", f"{report.company_name} ({report.symbol})  ·  confidence: {report.confidence}", "=" * 60]
    q = data.quote
    if q:
        sign = "+" if q.change >= 0 else ""
        lines.append(f"Price: {_fmt_num(q.price)} {q.currency}   {sign}{_fmt_num(q.change)} ({sign}{_fmt_num(q.change_percent)}%)")
    else:
        lines.append("Price: not available")
    lines += [f"Fetched: {data.fetched_at}", "", "SUMMARY", report.summary, "", "KEY METRICS"]
    for m in report.key_metrics:
        lines += [f"  • {m.label}: {m.value}", f"      {m.interpretation}"]
    lines += ["", "BULL CASE", *[f"  + {b}" for b in report.thesis.bull], "", "BEAR CASE", *[f"  - {b}" for b in report.thesis.bear]]
    lines += ["", "VALUATION CONTEXT", report.valuation_context, "", "RISKS", *[f"  • {r}" for r in report.risks]]
    lines += ["", "THINGS TO INVESTIGATE", *[f"  • {t}" for t in report.things_to_investigate], ""]
    if data.news:
        lines.append("RECENT NEWS")
        for n in data.news:
            lines += [f"  • {n.title} ({n.source}, {n.published_at})", f"    {n.url}"]
        lines.append("")
    print("\n".join(lines))


def _print_critique(critique: Critique, revised: bool) -> None:
    lines = ["CRITIC REVIEW" + ("  (report was revised)" if revised else ""), "-" * 60]
    fc = critique.fabrication_check
    lines.append(f"Fabrication check: {'PASSED' if fc.passed else 'FAILED'} — {fc.details}")
    lines.append(f"Suggested confidence: {critique.suggested_confidence}")
    if critique.issues:
        lines.append("Issues:")
        for i in critique.issues:
            lines += [f"  [{i.severity}] {i.field}: {i.problem}", f"      fix: {i.fix}"]
    else:
        lines.append("Issues: none")
    lines += [f"Assessment: {critique.overall_assessment}", ""]
    print("\n".join(lines))


def _print_candidates(result: DiscoveryResult) -> None:
    lines = ["", f"DISCOVERY · goal: {result.goal}", "=" * 60, f"Interpreted as: {result.interpretation}", ""]
    if not result.candidates:
        lines.append("No candidates survived validation and filtering.")
    else:
        for c in result.candidates:
            lines.append(f"  {c.symbol} — {c.name}   cap: {_fmt_cap(c.market_cap)}  P/E: {_fmt_num(c.pe_ratio)}  [{c.source}]")
            lines.append(f"      {c.rationale}")
        lines += ["", "Next: stocks research <SYMBOL> to deep-dive any of these."]
    print("\n".join(lines + [""]))


def _on_event(ctx) -> None:
    print("\n" + format_event(ctx.extra["_event"]), file=sys.stderr)


@app.command()
def research(
    symbol: str,
    cheap: bool = typer.Option(False, "--cheap", help="Workhorse critic, skip revision."),
    fresh: bool = typer.Option(False, "--fresh", help="Bypass the data + report caches."),
) -> None:
    """Deep-dive one ticker through the research + critic pipeline."""
    ticker = symbol.upper()
    mode: ReviewMode = "cheap" if cheap else "thorough"
    print(f"Researching {ticker} ({mode}{', fresh' if fresh else ''})...", file=sys.stderr)

    async def run() -> ResearchResult:
        async with with_run("research", ticker, mode) as ctx:
            res = await research_ticker_cached(ticker, mode, fresh=fresh)
            ctx.extra["_res"] = res
        _on_event(ctx)
        return res

    res = asyncio.run(run())
    _print_report(res.ticker, res.report)
    _print_critique(res.critique, res.revised)


@app.command()
def discover(goal: list[str]) -> None:
    """Discover candidate tickers for a free-text investment goal."""
    text = " ".join(goal).strip()
    if not text:
        typer.echo('Usage: stocks discover "<goal>"', err=True)
        raise typer.Exit(1)
    print(f"Discovering ideas for: {text}", file=sys.stderr)

    async def run() -> DiscoveryResult:
        async with with_run("discover", text) as ctx:
            res = await discover_ideas(text)
        _on_event(ctx)
        return res

    _print_candidates(asyncio.run(run()))


@app.command()
def usage() -> None:
    """Print the rolling usage / cost summary."""
    print(format_rollup())


@app.command()
def ledger() -> None:
    """Score pending calls and print the call ledger."""

    async def run() -> list[ledger_mod.Call]:
        await ledger_mod.evaluate_pending()
        return ledger_mod.list_calls()

    calls = asyncio.run(run())
    summary = ledger_mod.summarize(calls)

    console.print()
    console.print("[bold]CALL LEDGER[/bold]")
    console.print(f"total calls: {summary.total_calls}   scored calls: {summary.scored_calls}")
    for horizon in ledger_mod.HORIZON_DAYS:
        hit = summary.hit_rate.get(horizon)
        avg = summary.avg_excess.get(horizon)
        n = summary.n_directional.get(horizon, 0)
        console.print(
            f"{horizon}: hit rate {_fmt_pct(hit)}"
            f"{f' · n={n}' if n else ''}   avg excess {_fmt_pct(avg)}"
        )
    conf = "   ".join(
        f"{c}: {_fmt_pct(summary.hit_rate_by_confidence.get(c))}"
        for c in ("low", "medium", "high")
    )
    console.print(f"1m by confidence: {conf}")

    table = Table(title="Most recent 20 calls")
    for col in ("Date", "Symbol", "Mode", "Stance", "Confidence", "Price", "1w", "1m", "3m"):
        table.add_column(col)
    for call in calls[:20]:
        row = [
            call.as_of,
            call.symbol,
            call.mode,
            call.stance or "n/a",
            call.confidence,
            f"${call.price:.2f}" if call.price is not None else "n/a",
        ]
        for horizon in ledger_mod.HORIZON_DAYS:
            outcome = call.outcomes.get(horizon)
            if outcome is None:
                row.append("n/a")
            else:
                row.append(f"{_fmt_pct(outcome.fwd_return)} / {_fmt_pct(outcome.excess)}")
        table.add_row(*row)
    console.print(table)


@app.command()
def ledger_backfill() -> None:
    """Seed the ledger from cached research reports."""
    report_dir = CACHE_DIR / "report"
    inserted = 0
    skipped = 0
    if not report_dir.exists():
        console.print("No report cache directory found.")
        return

    for path in sorted(report_dir.glob("*.json")):
        match = CACHE_REPORT_RE.match(path.name)
        if not match:
            skipped += 1
            continue
        _symbol, _day, mode = match.groups()
        try:
            entry = json.loads(path.read_text())
            result = ResearchResult.model_validate(entry["value"])
            before = _call_count()
            ledger_mod.record_call(result, mode)
            inserted += _call_count() - before
        except Exception:
            skipped += 1
    console.print(f"Backfilled {inserted} cached reports into the ledger.")
    if skipped:
        console.print(f"Skipped {skipped} unparseable cache files.", style="yellow")


if __name__ == "__main__":
    app()
