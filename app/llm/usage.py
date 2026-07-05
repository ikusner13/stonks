"""Wide-event usage tracking. One canonical JSONL event per run; `stocks usage`
sums it. Port of the original llm/usage.ts (AsyncLocalStorage -> contextvars).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal

from pydantic_ai.exceptions import ModelHTTPError

from ..config import CACHE_DIR

USAGE_LOG = CACHE_DIR / "usage.jsonl"
logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {429, 502, 503, 529}

CallSite = Literal[
    "research", "critique", "revise", "re-critique", "discover-plan", "discover-rationale"
]


@dataclass
class CallUsage:
    call_site: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int  # input tokens served from cache
    cost_usd: float  # real $ from OpenRouter usage accounting (best-effort)
    duration_ms: int


@dataclass
class RunContext:
    kind: str  # research | discover
    subject: str  # symbol or goal
    mode: str  # thorough | cheap | …
    calls: list[CallUsage] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


_store: ContextVar[RunContext | None] = ContextVar("usage_run", default=None)


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _cost(result: Any) -> float:
    """OpenRouter's USD cost isn't a first-class field in pydantic-ai's RunUsage,
    so dig it out of the usage details / provider_details where it lands."""
    details = getattr(result.usage, "details", None) or {}
    for k in ("cost", "total_cost", "cost_usd"):
        if isinstance(details, dict) and k in details:
            return _num(details[k])
    pd = getattr(result.response, "provider_details", None)
    if isinstance(pd, dict):
        if "cost" in pd:
            return _num(pd["cost"])
        usage = pd.get("usage")
        if isinstance(usage, dict) and "cost" in usage:
            return _num(usage["cost"])
    return 0.0


def record(call_site: str, result: Any, duration_ms: int) -> None:
    """Record one LLM call's usage into the active run (if any)."""
    ctx = _store.get()
    if ctx is None:
        return
    u = result.usage
    ctx.calls.append(
        CallUsage(
            call_site=call_site,
            model=getattr(result.response, "model_name", None) or "unknown",
            input_tokens=int(_num(u.input_tokens)),
            output_tokens=int(_num(u.output_tokens)),
            cached_input_tokens=int(_num(u.cache_read_tokens)),
            cost_usd=_cost(result),
            duration_ms=duration_ms,
        )
    )


def _settings_for_attempt(model_settings: Any, attempt: int, max_attempts: int) -> Any:
    if attempt != max_attempts or not isinstance(model_settings, dict):
        return model_settings

    provider = model_settings.get("openrouter_provider")
    if not isinstance(provider, dict) or provider.get("allow_fallbacks") is not False:
        return model_settings

    copied = dict(model_settings)
    copied_provider = dict(provider)
    copied_provider.pop("allow_fallbacks", None)
    copied["openrouter_provider"] = copied_provider
    return copied


async def run_tracked(
    call_site: str, agent: Any, user_prompt: Any, model_settings: Any, model: Any = None
) -> Any:
    """Time an agent run, record its usage, return the result unchanged.

    ``model`` optionally overrides the agent's bound model (the critic chain
    swaps premium/workhorse per call)."""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        kwargs: dict[str, Any] = {
            "model_settings": _settings_for_attempt(model_settings, attempt, max_attempts)
        }
        if model is not None:
            kwargs["model"] = model
        start = time.monotonic()
        try:
            result = await agent.run(user_prompt, **kwargs)
        except ModelHTTPError as exc:
            if exc.status_code not in _RETRYABLE_STATUS_CODES or attempt == max_attempts:
                raise
            logger.warning(
                "LLM call failed with retryable HTTP status",
                extra={"call_site": call_site, "status": exc.status_code, "attempt": attempt},
            )
            await asyncio.sleep(2 * attempt + random.uniform(0, 1))
            continue
        record(call_site, result, int((time.monotonic() - start) * 1000))
        return result

    raise RuntimeError("unreachable")


def annotate_run(**fields: Any) -> None:
    ctx = _store.get()
    if ctx is not None:
        ctx.extra.update(fields)


def _totals(calls: list[CallUsage]) -> dict[str, Any]:
    return {
        "calls": len(calls),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "cached_input_tokens": sum(c.cached_input_tokens for c in calls),
        "cost_usd": sum(c.cost_usd for c in calls),
        "duration_ms": sum(c.duration_ms for c in calls),
    }


def _emit(ctx: RunContext) -> dict[str, Any]:
    event = {
        "ts": datetime.now(UTC).isoformat(),
        "kind": ctx.kind,
        "subject": ctx.subject,
        "mode": ctx.mode,
        "cached": ctx.extra.get("cached", False),
        "calls": [c.__dict__ for c in ctx.calls],
        "totals": _totals(ctx.calls),
        "python": sys.version.split()[0],
    }
    if "revised" in ctx.extra:
        event["revised"] = ctx.extra["revised"]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass  # never let logging break a run
    return event


@asynccontextmanager
async def with_run(kind: str, subject: str, mode: str = "thorough") -> AsyncIterator[RunContext]:
    """Run inside a usage-tracking context; emit one wide event when it settles.

    The emitted event is attached to ``ctx.extra['_event']`` for callers that
    want to print it.
    """
    ctx = RunContext(kind=kind, subject=subject, mode=mode)
    token = _store.set(ctx)
    try:
        yield ctx
    finally:
        ctx.extra["_event"] = _emit(ctx)
        _store.reset(token)


# --- Formatting -------------------------------------------------------------


def format_event(e: dict[str, Any]) -> str:
    t = e["totals"]
    head = (
        f"[usage] {e['kind']} {e['subject']} · mode={e['mode']}"
        f"{' · CACHE HIT (0 calls)' if e.get('cached') else ''}"
        f"{' · revised' if e.get('revised') else ''}"
    )
    lines = [head]
    for c in e["calls"]:
        lines.append(
            f"  {c['call_site']:<16} {c['model']:<28} "
            f"in={c['input_tokens']} (cached {c['cached_input_tokens']}) "
            f"out={c['output_tokens']} ${c['cost_usd']:.5f} {c['duration_ms']}ms"
        )
    lines.append(
        f"  TOTAL  calls={t['calls']} in={t['input_tokens']} "
        f"(cached {t['cached_input_tokens']}) out={t['output_tokens']} "
        f"${t['cost_usd']:.5f} {t['duration_ms']}ms"
    )
    return "\n".join(lines)


def read_events() -> list[dict[str, Any]]:
    try:
        raw = USAGE_LOG.read_text()
    except OSError:
        return []
    events = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def format_rollup(limit: int = 20) -> str:
    events = read_events()
    if not events:
        return "No usage recorded yet. Run `stocks research <SYMBOL>` first."

    recent = events[-limit:]
    lines = [f"USAGE — last {len(recent)} of {len(events)} runs", "=" * 72]
    for e in recent:
        t = e["totals"]
        lines.append(
            f"{e['ts']}  {e['kind']:<8} {e['subject'][:22]:<22} {e['mode']:<8} "
            f"calls={t['calls']} in={t['input_tokens']}(c{t['cached_input_tokens']}) "
            f"out={t['output_tokens']} ${t['cost_usd']:.5f}"
            f"{' [cache]' if e.get('cached') else ''}"
        )
    lines.append("-" * 72)
    cost = sum(e["totals"]["cost_usd"] for e in events)
    inp = sum(e["totals"]["input_tokens"] for e in events)
    cached = sum(e["totals"]["cached_input_tokens"] for e in events)
    out = sum(e["totals"]["output_tokens"] for e in events)
    pct = (cached / inp * 100) if inp else 0
    lines.append(
        f"ALL {len(events)} runs:  ${cost:.4f}  "
        f"in={inp} (cached {cached}, {pct:.1f}%)  out={out}"
    )
    return "\n".join(lines)
