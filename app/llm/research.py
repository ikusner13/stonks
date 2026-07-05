"""First-pass structured research report (workhorse model)."""

from __future__ import annotations

import json

from pydantic_ai import Agent

from ..indicators.schemas import IndicatorScorecard
from ..profiles.base import Profile
from ..schemas import TickerData, TickerReport
from .provider import workhorse_model, workhorse_settings
from .usage import run_tracked

SYSTEM = """You are a meticulous equity-research analyst. You write neutral, balanced reports.

ABSOLUTE RULES:
- You may ONLY reason over the numeric figures explicitly present in the provided JSON ground truth.
- NEVER invent, estimate, extrapolate, or recall from memory any number (prices, ratios, market cap, dates, etc.).
- In key_metrics, "value" must be the figure RESTATED verbatim from the JSON. Do not compute new numbers.
- If a figure needed for analysis is missing or null, explicitly say it is not available rather than guessing.
- A deterministic INDICATOR SCORECARD computed by code is provided alongside the ground truth. Treat its values as ground truth.
- In indicator_view, address every indicator whose signal is not "unavailable"; name disagreements between indicators explicitly instead of smoothing them over.
- Qualitative judgement (thesis, risks, interpretation) is encouraged, but it must be grounded in the provided data."""

_agent: Agent[None, TickerReport] | None = None


def _get_agent() -> Agent[None, TickerReport]:
    global _agent
    if _agent is None:
        _agent = Agent(workhorse_model(), output_type=TickerReport, system_prompt=SYSTEM)
    return _agent


def build_research_prompt(
    symbol: str, data: TickerData, scorecard: IndicatorScorecard, profile: Profile
) -> str:
    prompt = f"""Produce a structured research report for {symbol}.

Below is the ONLY ground truth you may use. Treat it as authoritative and complete; do not supplement it with outside knowledge of specific numbers.

```json
{json.dumps(data.model_dump(), indent=2)}
```

INDICATOR SCORECARD (computed deterministically by code):
```json
{json.dumps(scorecard.model_dump(), indent=2)}
```
"""
    if profile.research_stance:
        prompt += f"""
PROFILE CONTEXT:
{profile.research_stance}
"""
    prompt += """
Restate the relevant figures in key_metrics, explain why each matters, and build a balanced bull/bear thesis. Where data is missing, note it in things_to_investigate and let it lower your confidence."""
    return prompt


async def research_ticker(
    symbol: str, data: TickerData, scorecard: IndicatorScorecard, profile: Profile
) -> TickerReport:
    """First-pass structured report from the workhorse model, grounded in
    ``data`` and the deterministic ``scorecard``."""
    prompt = build_research_prompt(symbol, data, scorecard, profile)

    result = await run_tracked("research", _get_agent(), prompt, workhorse_settings())
    return result.output
