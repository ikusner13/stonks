"""Pydantic AI / OpenRouter model factories.

Lazy: importing this module (e.g. to print CLI usage) must not require the key.

- workhorse: cheap, high-volume calls (research draft, discovery). May chase the
  price floor via `sort: "price"`.
- premium: quality-critical calls (the critic chain). `allow_fallbacks: False`
  makes routing sticky so the cached prefix lands on the same provider endpoint
  across calls — Anthropic prompt caching only pays off on cache hits.

`data_collection: "deny"` keeps financial prompts off providers that log/train.
`usage.include` turns on OpenRouter usage accounting for real $ + cached tokens.
"""

from __future__ import annotations

from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ..config import OPENROUTER_API_KEY, PREMIUM_MODEL, WORKHORSE_MODEL

_provider: OpenRouterProvider | None = None


def _get_provider() -> OpenRouterProvider:
    global _provider
    if _provider is None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Get a key at "
                "https://openrouter.ai/keys and provide it via the environment "
                "(e.g. a .env file)."
            )
        _provider = OpenRouterProvider(api_key=OPENROUTER_API_KEY)
    return _provider


def workhorse_model() -> OpenRouterModel:
    return OpenRouterModel(WORKHORSE_MODEL, provider=_get_provider())


def premium_model() -> OpenRouterModel:
    return OpenRouterModel(PREMIUM_MODEL, provider=_get_provider())


def workhorse_settings(*, price_sort: bool = False) -> OpenRouterModelSettings:
    provider: dict = {"data_collection": "deny"}
    if price_sort:
        provider["sort"] = "price"
    return OpenRouterModelSettings(openrouter_provider=provider, openrouter_usage={"include": True})


def premium_settings() -> OpenRouterModelSettings:
    return OpenRouterModelSettings(
        openrouter_provider={"data_collection": "deny", "allow_fallbacks": False},
        openrouter_usage={"include": True},
    )
