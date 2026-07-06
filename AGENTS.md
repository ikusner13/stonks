# AGENTS.md

Conventions and hard rules for coding agents working in this repo.

## Conventions

- Package manager: `uv` (`uv sync`, `uv run ...`). No pip/poetry.
- Python 3.13, `ruff` line-length 100 (`pyproject.toml`).
- Tests: `pytest`, `asyncio_mode = "auto"` — `async def test_...` needs no
  decorator.

## Hard rules

**Code computes, the LLM interprets — never the reverse.** Every indicator in
`app/indicators/engine.py`, every confidence grade in
`app/indicators/confidence.py`, and every portfolio number in
`app/portfolio/` is deterministic Python. The LLM (`app/llm/`) only narrates
already-computed values; it never derives a number the app then treats as
fact. If you're adding a figure a report should state, compute it in code and
inject it into the prompt — don't ask the model to calculate it.

**Never cache a failure.** `app/cache.py::with_cache` only persists a
`produce()` result that returns non-`None`; anything that raises must keep
raising rather than being caught and cached as an empty/error placeholder.
This is what lets a transient outage self-heal on the next request instead of
being stuck behind a stale negative cache entry. See
[docs/architecture.md](docs/architecture.md) for the full caching table and
which per-source failures *do* still get embedded (and cached) inside an
otherwise-successful result.

**Read [docs/methodology.md](docs/methodology.md) before changing an
indicator threshold, a confidence weight, or any LLM system prompt.** It
documents the exact formulas, thresholds, and grounding rules as coded —
changing behavior without updating it makes the docs actively wrong, which is
worse than no docs.

## Before considering a change done

```bash
uv run pytest -q
uv run ruff check
```
