"""One discovery-flow eval run in a subprocess: WORKHORSE_MODEL from env.

usage: uv run python scripts/eval_discovery.py "GOAL" OUTPATH
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

from app.config import WORKHORSE_MODEL
from app.llm.discovery import discover_ideas
from app.llm.usage import with_run


async def main() -> int:
    try:
        goal, outpath = sys.argv[1], Path(sys.argv[2])
        async with with_run("eval-discover", goal[:40], "cheap") as ctx:
            result = await discover_ideas(goal)
        payload = {
            "workhorse": WORKHORSE_MODEL,
            "goal": goal,
            "result": result.model_dump(),
            "usage": ctx.extra["_event"],
        }
        outpath.parent.mkdir(parents=True, exist_ok=True)
        outpath.write_text(json.dumps(payload, indent=2, default=str))
        return 0
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
