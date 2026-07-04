import time

import pytest

from app import cache
from app.cache import read_cache, with_cache, write_cache

NS = "__pytest__"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    import shutil

    shutil.rmtree(cache.CACHE_DIR / NS, ignore_errors=True)


async def test_misses_then_hits_running_produce_once():
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        return {"v": calls}

    assert await with_cache(NS, "k", 60_000, produce) == ({"v": 1}, False)
    assert await with_cache(NS, "k", 60_000, produce) == ({"v": 1}, True)
    assert calls == 1


async def test_fresh_bypasses_warm_cache():
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        return calls

    await with_cache(NS, "k", 60_000, produce)
    assert await with_cache(NS, "k", 60_000, produce, fresh=True) == (2, False)
    assert calls == 2


def test_ttl_returns_value_before_expiry_and_none_after():
    write_cache(NS, "k", "val", 50)
    assert read_cache(NS, "k") == "val"
    time.sleep(0.06)
    assert read_cache(NS, "k") is None


def test_ttl_zero_never_expires():
    write_cache(NS, "k", "forever", 0)
    assert read_cache(NS, "k") == "forever"


def test_isolates_entries_by_key():
    write_cache(NS, "a", 1, 60_000)
    write_cache(NS, "b", 2, 60_000)
    assert read_cache(NS, "a") == 1
    assert read_cache(NS, "b") == 2
    assert read_cache(NS, "absent") is None


async def test_none_result_is_not_cached():
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        return None

    assert await with_cache(NS, "none", 60_000, produce) == (None, False)
    assert read_cache(NS, "none") is None
    assert await with_cache(NS, "none", 60_000, produce) == (None, False)
    assert calls == 2
