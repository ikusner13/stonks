"""Idea discovery: LLM screening plan -> validate against real data -> rationales."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from ..data import fetch_ticker_data
from ..data.screener import SCREEN_IDS, run_screen
from ..schemas import Candidate, DiscoveryResult
from .budget import check_budget
from .provider import workhorse_model, workhorse_settings
from .usage import run_tracked

# Discovery is non-critical: chase the cheapest provider endpoint.
_CHEAP = workhorse_settings(price_sort=True)


class _Filters(BaseModel):
    max_market_cap: float | None = Field(
        default=None, description="Maximum market cap in absolute dollars (e.g. 100e9), or null."
    )
    min_market_cap: float | None = Field(
        default=None, description="Minimum market cap in absolute dollars, or null."
    )
    max_pe: float | None = Field(default=None, description="Maximum trailing P/E ratio, or null.")
    sectors: list[str] | None = Field(
        default=None,
        description="Target Yahoo sectors when the goal clearly implies them, otherwise null.",
    )


class _ScreenPlan(BaseModel):
    strategy: str = Field(
        description=(
            'Which predefined Yahoo screen best matches the goal, or "none" if no '
            "predefined screen fits and the goal is purely thematic/qualitative. "
            f"Must be one of: {', '.join(SCREEN_IDS)}, none."
        )
    )
    theme_candidates: list[str] = Field(
        description=(
            "Ticker symbols you believe fit a thematic or qualitative goal that no "
            "predefined screen captures. May be empty when a strategy is used."
        )
    )
    filters: _Filters = Field(
        description=(
            "Numeric constraints extracted from the goal. These are ENFORCED IN CODE "
            "against real data, not by you."
        )
    )
    interpretation: str = Field(
        description="One-sentence restatement of how you understood the goal."
    )


class _Rationale(BaseModel):
    symbol: str
    rationale: str = Field(description="One qualitative sentence on why this stock fits the goal.")


class _Rationales(BaseModel):
    rationales: list[_Rationale]


PLAN_SYSTEM = """You are an equity-idea-discovery assistant. Given an investment goal, you propose which ONE predefined Yahoo screen best fits and/or name specific companies for a qualitative theme.

ABSOLUTE RULES:
- NEVER fabricate financial figures (market cap, P/E, price). Every number is independently fetched and validated downstream; invented numbers are useless.
- For "filters", only extract numeric thresholds the user actually expressed in the goal; convert market cap to absolute dollars (e.g. "$100B" -> 100e9). Use null when not specified.
- If the goal names or clearly implies a sector, set filters.sectors using exactly these Yahoo sector names: Technology, Healthcare, Financial Services, Consumer Cyclical, Consumer Defensive, Industrials, Energy, Basic Materials, Real Estate, Utilities, Communication Services. Otherwise leave sectors null.
- Prefer a predefined "strategy" when one cleanly matches; use theme_candidates for goals no screen captures.
- Treat the goal as the user's private research intent."""

RATIONALE_SYSTEM = """You write one-sentence, qualitative rationales explaining why each stock fits the user's research goal.

ABSOLUTE RULES:
- NEVER invent or restate specific numbers (prices, ratios, market cap). Keep rationales purely qualitative.
- Ground each rationale in the goal and the stock's role/business; do not fabricate facts."""

_plan_agent = None
_rationale_agent = None


def _get_plan_agent():
    global _plan_agent
    if _plan_agent is None:
        from pydantic_ai import Agent

        _plan_agent = Agent(workhorse_model(), output_type=_ScreenPlan, system_prompt=PLAN_SYSTEM)
    return _plan_agent


def _get_rationale_agent():
    global _rationale_agent
    if _rationale_agent is None:
        from pydantic_ai import Agent

        _rationale_agent = Agent(
            workhorse_model(), output_type=_Rationales, system_prompt=RATIONALE_SYSTEM
        )
    return _rationale_agent


def _passes(
    filters: _Filters, market_cap: float | None, pe: float | None, sector: str | None
) -> bool:
    if filters.max_market_cap is not None and (
        market_cap is None or market_cap > filters.max_market_cap
    ):
        return False
    if filters.min_market_cap is not None and (
        market_cap is None or market_cap < filters.min_market_cap
    ):
        return False
    if filters.max_pe is not None and (pe is None or pe > filters.max_pe):
        return False
    if filters.sectors is not None:
        sector_names = {s.lower() for s in filters.sectors}
        if sector is None or sector.lower() not in sector_names:
            return False
    return True


async def discover_ideas(goal: str, limit: int = 8) -> DiscoveryResult:
    check_budget()
    plan_result = await run_tracked(
        "discover-plan", _get_plan_agent(), f"Investment goal:\n{goal}\n\nPropose a screening plan.", _CHEAP
    )
    plan: _ScreenPlan = plan_result.output

    # Gather raw candidate symbols with provenance (and any screener-provided name).
    sources: dict[str, dict] = {}
    if plan.strategy != "none" and plan.strategy in SCREEN_IDS:
        for q in await asyncio.to_thread(run_screen, plan.strategy):
            sources.setdefault(q.symbol, {"source": "screener", "name": q.name})
    for sym in plan.theme_candidates:
        s = sym.strip().upper()
        if s:
            sources.setdefault(s, {"source": "theme", "name": None})

    # Validate + enrich each unique symbol against real data; enforce filters in code.
    async def enrich(symbol: str, meta: dict):
        try:
            data = await fetch_ticker_data(symbol)
        except Exception:
            return None
        market_cap = data.fundamentals.market_cap
        pe = data.fundamentals.pe_ratio
        sector = data.fundamentals.sector
        if data.quote is None and market_cap is None:  # likely delisted/hallucinated
            return None
        if not _passes(plan.filters, market_cap, pe, sector):
            return None
        return {
            "symbol": symbol,
            "source": meta["source"],
            "name": meta["name"] or symbol,
            "market_cap": market_cap,
            "pe_ratio": pe,
        }

    enriched = await asyncio.gather(*(enrich(s, m) for s, m in sources.items()))
    survivors = [e for e in enriched if e is not None][:limit]

    if not survivors:
        return DiscoveryResult(goal=goal, interpretation=plan.interpretation, candidates=[])

    listing = "\n".join(f"- {s['symbol']} (source: {s['source']})" for s in survivors)
    rat_result = await run_tracked(
        "discover-rationale",
        _get_rationale_agent(),
        f"Goal:\n{goal}\n\nWrite a one-sentence qualitative rationale for each of these validated tickers:\n{listing}",
        _CHEAP,
    )
    rationale_by = {r.symbol.upper(): r.rationale for r in rat_result.output.rationales}

    candidates = [
        Candidate(
            symbol=s["symbol"],
            name=s["name"],
            market_cap=s["market_cap"],
            pe_ratio=s["pe_ratio"],
            rationale=rationale_by.get(
                s["symbol"].upper(), f"Surfaced via {s['source']} as a fit for the stated goal."
            ),
            source=s["source"],
        )
        for s in survivors
    ]
    return DiscoveryResult(goal=goal, interpretation=plan.interpretation, candidates=candidates)
