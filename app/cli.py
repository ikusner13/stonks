"""CLI: `stocks research <SYMBOL>`, `stocks discover "<goal>"`, `stocks usage`.

Mirrors the original tsx CLI. The report goes to stdout; usage/progress to stderr.
"""

from __future__ import annotations

import asyncio
import sys
from enum import Enum

import typer

from .llm.critic import ReviewMode
from .llm.discovery import discover_ideas
from .llm.pipeline import research_ticker_cached
from .llm.usage import format_event, format_rollup, with_run
from .schemas import Critique, DiscoveryResult, ResearchResult, TickerData, TickerReport
from . import db
from .jobs import LAST_RUN_PREFIX, Job, build_jobs

app = typer.Typer(add_completion=False, help="LLM-driven equity research assistant.")


class ProfileOption(str, Enum):
    penny = "penny"
    largecap = "largecap"


def _fmt_num(n: float | None) -> str:
    return "n/a" if n is None else f"{n:,.2f}".rstrip("0").rstrip(".")


def _fmt_cap(n: float | None) -> str:
    if n is None:
        return "n/a"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if n >= div:
            return f"${n / div:.2f}{suf}"
    return f"${_fmt_num(n)}"


def _fmt_timedelta(job: Job) -> str:
    if job.at_hour_utc is not None:
        return f"daily @ {job.at_hour_utc:02d}:00 UTC"
    assert job.cadence is not None
    seconds = int(job.cadence.total_seconds())
    parts = (
        (seconds // 86400, "d"),
        ((seconds % 86400) // 3600, "h"),
        ((seconds % 3600) // 60, "m"),
        (seconds % 60, "s"),
    )
    compact = "".join(f"{value}{unit}" for value, unit in parts if value)
    return f"every {compact or '0s'}"


def _job_last_run(job: Job) -> str:
    return db.get_setting(f"{LAST_RUN_PREFIX}{job.name}") or "never"


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
    profile: ProfileOption | None = typer.Option(
        None,
        "--profile",
        help="Override automatic research profile selection.",
    ),
) -> None:
    """Deep-dive one ticker through the research + critic pipeline."""
    ticker = symbol.upper()
    mode: ReviewMode = "cheap" if cheap else "thorough"
    print(f"Researching {ticker} ({mode}{', fresh' if fresh else ''})...", file=sys.stderr)

    async def run() -> ResearchResult:
        async with with_run("research", ticker, mode) as ctx:
            res = await research_ticker_cached(
                ticker,
                mode,
                profile_override=profile.value if profile else None,
                fresh=fresh,
            )
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
def jobs_list() -> None:
    """List registered background jobs and their last-run ledger values."""
    db.init_db()
    for job in build_jobs():
        typer.echo(f"{job.name}\t{_fmt_timedelta(job)}\tlast_run={_job_last_run(job)}")


@app.command()
def jobs_run(name: str) -> None:
    """Run one registered background job immediately without advancing its schedule."""
    db.init_db()
    for job in build_jobs():
        if job.name == name:
            asyncio.run(job.run())
            return
    typer.echo(f"unknown job: {name}", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
