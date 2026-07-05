"""Sweep model configurations over a fixed ticker suite."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

# ---- edit these two blocks between eval campaigns -------------------------
# Workhorse candidates: full cheap pipeline (draft + self-audit) on the candidate.
# Premium candidates: thorough pipeline with the incumbent workhorse drafting, so
# every premium critic reviews comparable drafts.
CONFIGS = [
    {"name": "wh-glm52", "workhorse": "z-ai/glm-5.2",
     "premium": "anthropic/claude-sonnet-4.6", "mode": "cheap"},
    {"name": "wh-kimik26", "workhorse": "moonshotai/kimi-k2.6",
     "premium": "anthropic/claude-sonnet-4.6", "mode": "cheap"},
    {"name": "wh-minimaxm3", "workhorse": "minimax/minimax-m3",
     "premium": "anthropic/claude-sonnet-4.6", "mode": "cheap"},
    {"name": "pm-sonnet46", "workhorse": "google/gemini-3.5-flash",
     "premium": "anthropic/claude-sonnet-4.6", "mode": "thorough"},
    {"name": "pm-opus48", "workhorse": "google/gemini-3.5-flash",
     "premium": "anthropic/claude-opus-4.8", "mode": "thorough"},
    {"name": "pm-gemini31pro", "workhorse": "google/gemini-3.5-flash",
     "premium": "google/gemini-3.1-pro-preview", "mode": "thorough"},
]
TICKERS = ["AAPL", "NVDA", "JPM", "CAVA", "OSCR"]
CONCURRENCY = 3
TIMEOUT_S = 420
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "eval_runner.py"
ERROR_TAIL_CHARS = 4000


@dataclass
class RunSummary:
    config: str
    ticker: str
    status: str
    cost_usd: float | None = None
    in_tokens: int | None = None
    cached: int | None = None
    out_tokens: int | None = None
    revised: bool | None = None
    fab_check_passed: bool | None = None
    report_confidence: str | None = None
    suggested_confidence: str | None = None
    duration_ms: int | None = None


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _format_float(value: float | None, places: int = 5) -> str:
    return "" if value is None else f"{value:.{places}f}"


def _format_int(value: int | None) -> str:
    return "" if value is None else str(value)


def _format_bool(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


async def _communicate_with_timeout(
    proc: asyncio.subprocess.Process, timeout_s: int
) -> tuple[bytes, bytes, bool]:
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        return stdout, stderr, False
    except TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return stdout, stderr, True


async def _prewarm_ticker(ticker: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(RUNNER),
        "--prewarm",
        ticker,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr, _timed_out = await _communicate_with_timeout(proc, TIMEOUT_S)
    return proc.returncode or 0, stderr.decode(errors="replace")


async def _run_eval(
    cfg: dict[str, str],
    ticker: str,
    outpath: Path,
    semaphore: asyncio.Semaphore,
    completed: list[int],
    total: int,
) -> RunSummary:
    label = f"{cfg['name']}__{ticker}"
    env = {
        **os.environ,
        "WORKHORSE_MODEL": cfg["workhorse"],
        "PREMIUM_MODEL": cfg["premium"],
    }

    async with semaphore:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(RUNNER),
            ticker,
            cfg["mode"],
            str(outpath),
            cwd=ROOT,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr, timed_out = await _communicate_with_timeout(proc, TIMEOUT_S)
        elapsed_s = time.monotonic() - start

    completed[0] += 1
    if timed_out:
        status = "timeout"
        err = f"Timed out after {TIMEOUT_S}s\n{stderr.decode(errors='replace')}"
        _write_error(outpath, err)
        print(f"[{completed[0]}/{total}] timeout  {label}  {elapsed_s:.1f}s")
        return RunSummary(cfg["name"], ticker, status)

    if proc.returncode != 0:
        status = f"error:{proc.returncode}"
        _write_error(outpath, stderr.decode(errors="replace"))
        print(f"[{completed[0]}/{total}] error  {label}  {elapsed_s:.1f}s")
        return RunSummary(cfg["name"], ticker, status)

    summary = _summary_from_result(cfg["name"], ticker, outpath)
    cost = _format_float(summary.cost_usd)
    print(f"[{completed[0]}/{total}] ok  {label}  ${cost or '0.00000'}  {elapsed_s:.1f}s")
    return summary


def _write_error(outpath: Path, stderr: str) -> None:
    error_path = outpath.with_suffix(".error.txt")
    error_path.write_text(stderr[-ERROR_TAIL_CHARS:])


def _summary_from_result(config: str, ticker: str, outpath: Path) -> RunSummary:
    data = json.loads(outpath.read_text())
    usage = data.get("usage", {})
    totals = usage.get("totals", {})
    critique = data.get("critique", {})
    fab = critique.get("fabrication_check", {})
    report = data.get("report", {})
    return RunSummary(
        config=config,
        ticker=ticker,
        status="ok",
        cost_usd=totals.get("cost_usd"),
        in_tokens=totals.get("input_tokens"),
        cached=totals.get("cached_input_tokens"),
        out_tokens=totals.get("output_tokens"),
        revised=data.get("revised"),
        fab_check_passed=fab.get("passed"),
        report_confidence=report.get("confidence"),
        suggested_confidence=critique.get("suggested_confidence"),
        duration_ms=totals.get("duration_ms"),
    )


def _summary_table(rows: list[RunSummary]) -> str:
    headers = [
        "config",
        "ticker",
        "status",
        "cost_usd",
        "in_tokens",
        "cached",
        "out_tokens",
        "revised",
        "fab_check_passed",
        "report_confidence",
        "suggested_confidence",
        "duration_ms",
    ]
    table = [_markdown_row(headers), _markdown_row(["---"] * len(headers))]
    for row in rows:
        table.append(
            _markdown_row(
                [
                    row.config,
                    row.ticker,
                    row.status,
                    _format_float(row.cost_usd),
                    _format_int(row.in_tokens),
                    _format_int(row.cached),
                    _format_int(row.out_tokens),
                    _format_bool(row.revised),
                    _format_bool(row.fab_check_passed),
                    row.report_confidence or "",
                    row.suggested_confidence or "",
                    _format_int(row.duration_ms),
                ]
            )
        )
    table.extend(["", "## Per-config aggregates", ""])
    table.extend(_aggregate_table(rows))
    return "\n".join(table)


def _aggregate_table(rows: list[RunSummary]) -> list[str]:
    headers = [
        "config",
        "mean_cost_usd",
        "mean_duration_ms",
        "fabrication_failures",
        "revisions",
        "errors",
    ]
    table = [_markdown_row(headers), _markdown_row(["---"] * len(headers))]
    for cfg in CONFIGS:
        config_rows = [row for row in rows if row.config == cfg["name"]]
        ok_rows = [row for row in config_rows if row.status == "ok"]
        costs = [row.cost_usd for row in ok_rows if row.cost_usd is not None]
        durations = [row.duration_ms for row in ok_rows if row.duration_ms is not None]
        fab_failures = sum(row.fab_check_passed is False for row in ok_rows)
        revisions = sum(row.revised is True for row in ok_rows)
        errors = sum(row.status != "ok" for row in config_rows)
        table.append(
            _markdown_row(
                [
                    cfg["name"],
                    _format_float(mean(costs) if costs else None),
                    _format_float(mean(durations) if durations else None, places=1),
                    str(fab_failures),
                    str(revisions),
                    str(errors),
                ]
            )
        )
    return table


def _markdown_row(values: list[Any]) -> str:
    return "| " + " | ".join(str(value) for value in values) + " |"


async def main() -> int:
    results_dir = ROOT / "eval_results" / _stamp()
    results_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "configs": CONFIGS,
        "tickers": TICKERS,
        "started_at": _utc_now(),
        "dropped_tickers": [],
    }
    manifest_path = results_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    tickers: list[str] = []
    for ticker in TICKERS:
        code, stderr = await _prewarm_ticker(ticker)
        if code == 2:
            manifest["dropped_tickers"].append({"ticker": ticker, "reason": stderr.strip()})
            print(f"[prewarm] drop {ticker}: insufficient data")
            continue
        if code != 0:
            manifest["dropped_tickers"].append({"ticker": ticker, "reason": stderr.strip()})
            print(f"[prewarm] drop {ticker}: failed")
            continue
        tickers.append(ticker)
        print(f"[prewarm] ok {ticker}")
    manifest["tickers"] = tickers
    manifest_path.write_text(json.dumps(manifest, indent=2))

    total = len(CONFIGS) * len(tickers)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    completed = [0]
    tasks = [
        asyncio.create_task(
            _run_eval(
                cfg,
                ticker,
                results_dir / f"{cfg['name']}__{ticker}.json",
                semaphore,
                completed,
                total,
            )
        )
        for cfg in CONFIGS
        for ticker in tickers
    ]
    rows = await asyncio.gather(*tasks) if tasks else []
    summary = _summary_table(rows)
    (results_dir / "summary.md").write_text(summary)
    print(summary)

    ok_count = sum(row.status == "ok" for row in rows)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
