"""CLI: `stocks research <SYMBOL>`, `stocks discover "<goal>"`, `stocks usage`.

Mirrors the original tsx CLI. The report goes to stdout; usage/progress to stderr.
"""

from __future__ import annotations

import asyncio
import sys
from enum import Enum

import typer

from . import config, db
from .alerts import init_alerts_db
from .jobs import LAST_RUN_PREFIX, Job, build_jobs
from .llm.critic import ReviewMode
from .llm.discovery import discover_ideas
from .llm.pipeline import research_ticker_cached
from .llm.usage import format_event, format_rollup, with_run
from .schemas import Critique, DiscoveryResult, ResearchResult, TickerData, TickerReport

app = typer.Typer(add_completion=False, help="LLM-driven equity research assistant.")
broker_app = typer.Typer(help="Read-only broker connection and sync commands.")
app.add_typer(broker_app, name="broker")


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
    init_alerts_db()
    for job in build_jobs():
        typer.echo(f"{job.name}\t{_fmt_timedelta(job)}\tlast_run={_job_last_run(job)}")


@app.command()
def jobs_run(name: str) -> None:
    """Run one registered background job immediately without advancing its schedule."""
    db.init_db()
    init_alerts_db()
    for job in build_jobs():
        if job.name == name:
            asyncio.run(job.run())
            return
    typer.echo(f"unknown job: {name}", err=True)
    raise typer.Exit(1)


@broker_app.command("connect")
def broker_connect() -> None:
    """Register/connect the configured SnapTrade user and print the portal URL."""
    from .broker.snaptrade import connection_portal_url, register_user

    async def run() -> tuple[str | None, str]:
        user_secret: str | None = None
        if not config.SNAPTRADE_USER_SECRET:
            user_secret = await register_user()
            config.SNAPTRADE_USER_SECRET = user_secret
        return user_secret, await connection_portal_url()

    try:
        user_secret, portal_url = asyncio.run(run())
    except Exception as exc:
        typer.echo(f"SnapTrade connect failed: {exc}", err=True)
        if "1012" in str(exc):
            typer.echo(
                "Personal SnapTrade keys auto-provision their user at signup, so "
                "registerUser is unavailable. Set SNAPTRADE_USER_ID to the user shown "
                "by listUsers (usually your signup email) and SNAPTRADE_USER_SECRET "
                "from the SnapTrade dashboard, then rerun.",
                err=True,
            )
        raise typer.Exit(1) from exc

    if user_secret:
        typer.echo(f"SNAPTRADE_USER_SECRET={user_secret}")
    typer.echo(portal_url)


@broker_app.command("sync")
def broker_sync(
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch and diff only."),
    since: str = typer.Option(
        "", "--since", help="Backfill activities from this ISO date instead of the last sync."
    ),
) -> None:
    """Sync local holdings/cash from the configured SnapTrade account."""
    from datetime import date as _date

    from .broker.sync import run_sync

    since_date = None
    if since:
        try:
            since_date = _date.fromisoformat(since)
        except ValueError as exc:
            typer.echo("--since must be an ISO date YYYY-MM-DD", err=True)
            raise typer.Exit(1) from exc

    try:
        result = asyncio.run(run_sync(dry_run=dry_run, since=since_date))
    except Exception as exc:
        typer.echo(f"Broker sync failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    status = "dry-run" if dry_run else "applied"
    typer.echo(f"Broker sync {status} @ {result.asof}")
    typer.echo(
        "holdings: "
        f"{len(result.diff.to_upsert)} upsert, "
        f"{len(result.diff.to_remove)} remove, "
        f"{result.diff.unchanged} unchanged"
    )
    typer.echo(
        "cash: "
        f"${result.diff.cash_before:,.2f} -> ${result.diff.cash_after:,.2f}; "
        f"activities: {result.imported_activities} imported, "
        f"{result.skipped_activities} skipped"
    )
    for warning in result.warnings:
        typer.echo(f"warning: {warning}", err=True)


if __name__ == "__main__":
    app()
