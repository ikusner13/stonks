"""Dependency-light persistent KV: one JSON file per entry under .cache/.

No Redis/SQLite for transient API/report caches — a personal tool wants zero
infra. Shared by the CLI and the web server. Port of the original lib/cache.ts.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .config import CACHE_DIR

T = TypeVar("T")

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]")
_inflight: dict[str, tuple[asyncio.Lock, int]] = {}


def _path_for(namespace: str, key: str) -> Path:
    safe = _UNSAFE.sub("_", key)
    return CACHE_DIR / namespace / f"{safe}.json"


def read_cache(namespace: str, key: str) -> Any | None:
    try:
        entry = json.loads(_path_for(namespace, key).read_text())
    except (OSError, ValueError):
        return None
    expires_at = entry.get("expiresAt", 0)
    if expires_at and time.time() * 1000 > expires_at:
        return None
    return entry.get("value")


def write_cache(namespace: str, key: str, value: Any, ttl_ms: float) -> None:
    try:
        path = _path_for(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        expires_at = time.time() * 1000 + ttl_ms if ttl_ms > 0 else 0
        path.write_text(json.dumps({"expiresAt": expires_at, "value": value}))
    except OSError:
        # A cache write failure must never break the call it wraps.
        pass


async def with_cache(
    namespace: str,
    key: str,
    ttl_ms: float,
    produce: Callable[[], Awaitable[T]],
    *,
    fresh: bool = False,
) -> tuple[T, bool]:
    """Read-through cache. Returns ``(value, hit)``. ``fresh`` forces a miss."""
    if not fresh:
        cached = read_cache(namespace, key)
        if cached is not None:
            return cached, True

    inflight_key = f"{namespace}:{key}"
    lock, refcount = _inflight.get(inflight_key, (asyncio.Lock(), 0))
    _inflight[inflight_key] = (lock, refcount + 1)
    try:
        async with lock:
            if not fresh:
                cached = read_cache(namespace, key)
                if cached is not None:
                    return cached, True
            value = await produce()
            if value is not None:
                write_cache(namespace, key, value, ttl_ms)
            return value, False
    finally:
        lock2, refcount2 = _inflight[inflight_key]
        if refcount2 <= 1:
            _inflight.pop(inflight_key, None)
        else:
            _inflight[inflight_key] = (lock2, refcount2 - 1)
