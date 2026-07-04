"""Skeptical critic chain: audit -> (revise) -> re-critique, with a programmatic
fabrication check and a cached ground-truth prefix shared across the chain.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic_ai import Agent, CachePoint

from ..schemas import Critique, FabricationCheck, TickerData, TickerReport
from .provider import premium_model, premium_settings, workhorse_model, workhorse_settings
from .research import research_ticker
from .usage import annotate_run, run_tracked

ReviewMode = Literal["thorough", "cheap"]

_MAGNITUDE = {
    "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
    "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
}

_NUM_RE = re.compile(
    r"-?\$?(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|[kmbt]\b|thousand|million|billion|trillion)?",
    re.IGNORECASE,
)


def _number_values(match: re.Match[str]) -> list[float] | None:
    try:
        n = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (match.group(2) or "").lower()
    if unit and unit != "%" and unit in _MAGNITUDE:
        n *= _MAGNITUDE[unit]
    return [n, n / 100] if unit == "%" else [n]


def parse_numbers(text: str) -> list[list[float]]:
    """Candidate interpretations per numeric token. Handles $, commas, decimals,
    a % sign, an adjacent magnitude letter (B/M), and a spelled-out magnitude word.
    A "25%" token yields both 25 and 0.25 since ratios may be stored as fractions."""
    out: list[list[float]] = []
    for m in _NUM_RE.finditer(text):
        values = _number_values(m)
        if values is not None:
            out.append(values)
    return out


def _prose_numbers(text: str) -> list[list[float]]:
    out: list[list[float]] = []
    for m in _NUM_RE.finditer(text):
        token = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        raw = m.group(0)
        has_unit = "$" in raw or unit in {"%", *list(_MAGNITUDE)}
        is_bare_integer = "." not in token and not has_unit

        if text[m.end():m.end() + 2].lower() in {"-k", "-q"}:
            continue
        if is_bare_integer:
            n = int(token)
            if 0 <= n <= 10:
                continue
            if 1900 <= n <= 2100:
                continue

        values = _number_values(m)
        if values is not None:
            out.append(values)
    return out


def _collect_allowed(data: TickerData) -> list[float]:
    """Every number anywhere in the ground truth — quote, fundamentals, and figures
    quoted in news HEADLINES — is fair for the report to restate. Timestamps and
    URLs are excluded to avoid spurious date-fragment matches."""
    parts = [json.dumps(data.fundamentals.model_dump())]
    if data.quote:
        parts.append(json.dumps(data.quote.model_dump()))
    for n in data.news:
        parts.append(n.title)
    allowed: list[float] = []
    for p in parts:
        for values in parse_numbers(p):
            allowed.extend(values)
    if data.financials:
        allowed.extend(data.financials.numeric_values())
    if data.macro:
        allowed.extend(data.macro.numeric_values())
    return allowed


def _is_grounded(n: float, allowed: list[float], tol: float = 0.02) -> bool:
    """Grounded if it matches an allowed number within a small relative tolerance,
    absorbing rounding/unit restatements (e.g. 2.5T vs 2.49e12)."""
    an = abs(n)  # sign is conveyed by words ("down 0.61%"), compare magnitude
    for a in allowed:
        aa = abs(a)
        scale = max(aa, an)
        if scale == 0:
            return True
        if abs(aa - an) / scale <= tol:
            return True
    return False


def check_fabrication(report: TickerReport, data: TickerData) -> FabricationCheck:
    allowed = _collect_allowed(data)
    candidates: list[tuple[str, list[float]]] = []
    for i, m in enumerate(report.key_metrics):
        for values in parse_numbers(m.value):
            candidates.append((f"key_metrics[{i}] ({m.label})", values))
        for values in _prose_numbers(m.interpretation):
            candidates.append((f"key_metrics[{i}].interpretation", values))
    for values in parse_numbers(report.valuation_context):
        candidates.append(("valuation_context", values))
    for values in _prose_numbers(report.summary):
        candidates.append(("summary", values))
    for i, text in enumerate(report.thesis.bull):
        for values in _prose_numbers(text):
            candidates.append((f"thesis.bull[{i}]", values))
    for i, text in enumerate(report.thesis.bear):
        for values in _prose_numbers(text):
            candidates.append((f"thesis.bear[{i}]", values))
    for i, text in enumerate(report.risks):
        for values in _prose_numbers(text):
            candidates.append((f"risks[{i}]", values))
    for i, text in enumerate(report.things_to_investigate):
        for values in _prose_numbers(text):
            candidates.append((f"things_to_investigate[{i}]", values))

    unmatched = [
        (src, values)
        for src, values in candidates
        if not any(_is_grounded(v, allowed) for v in values)
    ]
    if not unmatched:
        return FabricationCheck(
            passed=True, details="All report figures trace to the provided data."
        )
    listing = "; ".join(f"{values[0]} in {src}" for src, values in unmatched)
    return FabricationCheck(
        passed=False,
        details=f"Figures not found in ground-truth data (within tolerance): {listing}.",
    )


# ONE shared system for every critic-chain call keeps the cacheable ground-truth
# prefix identical across audit/revise/re-critique; the per-call task lives in the
# variable tail, after the cache breakpoint, so it never invalidates the cache.
CRITIC_SYSTEM = """You are a skeptical senior equity-research analyst working against a fixed ground-truth JSON dataset. You perform exactly one task per request — AUDIT a report, or REVISE a report after an audit — as stated in the user message.

ABSOLUTE RULES (both tasks):
- You may ONLY reason over the numeric figures explicitly present in the ground-truth JSON.
- NEVER invent, estimate, extrapolate, or recall from memory any number (prices, ratios, market cap, dates).
- In key_metrics, "value" must be a figure RESTATED verbatim from the JSON.
- If a figure is missing or null, say it is not available rather than guessing.
- Default to skepticism. Sparse or null-heavy data should mean lower confidence.

Follow the task instructions in the user message exactly."""

AUDIT_GUIDANCE = """Audit for: support (is every qualitative claim grounded?), traceability (is every number present in the ground truth? treat the fabrication hint as authoritative for hard fabrications, then look for any it missed), balance (fair bull/bear?), completeness (material risks omitted?), calibration (confidence justified by data completeness?). Report concrete, actionable issues; reserve "high" severity for fabricated numbers or materially misleading claims."""


def _ground_truth(data: TickerData) -> str:
    return (
        "GROUND TRUTH (the ONLY numbers you may use):\n```json\n"
        + json.dumps(data.model_dump(), indent=2)
        + "\n```"
    )


_critic_agent: Agent[None, Critique] | None = None
_revise_agent: Agent[None, TickerReport] | None = None


def _get_critic_agent() -> Agent[None, Critique]:
    global _critic_agent
    if _critic_agent is None:
        _critic_agent = Agent(
            workhorse_model(), output_type=Critique, system_prompt=CRITIC_SYSTEM
        )
    return _critic_agent


def _get_revise_agent() -> Agent[None, TickerReport]:
    global _revise_agent
    if _revise_agent is None:
        _revise_agent = Agent(
            premium_model(), output_type=TickerReport, system_prompt=CRITIC_SYSTEM
        )
    return _revise_agent


async def _run_critique(
    report: TickerReport, data: TickerData, gt: str, call_site: str, mode: ReviewMode
) -> Critique:
    fab = check_fabrication(report, data)
    hint = "PASSED" if fab.passed else f"FAILED — {fab.details}"
    tail = f"""TASK: AUDIT the report below against the ground truth above.

PROGRAMMATIC FABRICATION CHECK (heuristic hint): {hint}

{AUDIT_GUIDANCE}

REPORT UNDER REVIEW:
```json
{json.dumps(report.model_dump(), indent=2)}
```"""

    # Cached prefix shared across the chain: gt, then a breakpoint, then the tail.
    prompt = [gt, CachePoint(ttl="1h"), tail]
    if mode == "cheap":
        result = await run_tracked(
            call_site, _get_critic_agent(), prompt, workhorse_settings()
        )
    else:
        result = await run_tracked(
            call_site, _get_critic_agent(), prompt, premium_settings(), model=premium_model()
        )
    critique: Critique = result.output

    # Never let the LLM override a hard-caught fabrication.
    if not fab.passed:
        critique.fabrication_check = FabricationCheck(
            passed=False,
            details=f"{fab.details} {critique.fabrication_check.details}".strip(),
        )
    return critique


async def critique_report(report: TickerReport, data: TickerData) -> Critique:
    """Single-shot critique (tests / direct callers)."""
    return await _run_critique(report, data, _ground_truth(data), "critique", "thorough")


async def research_ticker_reviewed(
    symbol: str, data: TickerData, mode: ReviewMode = "thorough"
) -> tuple[TickerReport, Critique, bool]:
    gt = _ground_truth(data)  # one cacheable prefix shared by every call below

    report = await research_ticker(symbol, data)
    critique = await _run_critique(report, data, gt, "critique", mode)

    # Cheap mode stops here: workhorse critique, no premium revision.
    needs_revision = mode == "thorough" and (
        not critique.fabrication_check.passed
        or any(i.severity in ("medium", "high") for i in critique.issues)
    )
    if not needs_revision:
        annotate_run(revised=False)
        return report, critique, False

    revise_tail = f"""TASK: REVISE the report for {symbol} to fix every problem found in the audit. Apply each fix. Remove any fabricated or unsupported figure entirely rather than replacing it with another guess. Keep the report balanced and grounded.

ORIGINAL REPORT:
```json
{json.dumps(report.model_dump(), indent=2)}
```

AUDIT ISSUES TO FIX:
```json
{json.dumps([i.model_dump() for i in critique.issues], indent=2)}
```"""

    result = await run_tracked(
        "revise", _get_revise_agent(), [gt, CachePoint(ttl="1h"), revise_tail], premium_settings()
    )
    revised_report: TickerReport = result.output

    final_critique = await _run_critique(revised_report, data, gt, "re-critique", mode)
    annotate_run(revised=True)
    return revised_report, final_critique, True
