"""Storage capacity, expiration, and concurrent legacy-update regression tests."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from utils.storage_backend import InMemoryStorage, StorageCapacityError


@pytest.fixture
def storage():
    instance = InMemoryStorage(max_threads=2, max_bytes=64)
    yield instance
    instance.shutdown()


def test_live_records_are_never_evicted_and_expired_records_release_capacity(storage):
    now = [0.0]
    storage._clock = lambda: now[0]
    storage.setex("a", 10, "first")
    storage.setex("b", 20, "second")
    with pytest.raises(StorageCapacityError):
        storage.setex("c", 20, "third")
    assert storage.get("a") == "first"
    now[0] = 10
    storage.setex("c", 20, "third")
    assert storage.get("a") is None
    assert storage.get("b") == "second"


def test_utf8_bytes_and_reserved_terminal_growth_count_toward_capacity(storage):
    storage.max_bytes = 16
    storage.reserve("a", 10, "😀", 8)
    with pytest.raises(StorageCapacityError):
        storage.setex("b", 10, "12345")
    storage.setex("b", 10, "1234")
    storage.commit_reserved("a", 10, "😀" * 3)
    assert storage.get("a") == "😀" * 3
    assert storage._reservations == {}


def test_inflight_reservation_pins_record_until_terminal_commit(storage):
    now = [0.0]
    storage._clock = lambda: now[0]
    storage.reserve("a", 1, "initial", 20)
    now[0] = 10
    assert storage.get("a") == "initial"
    storage.commit_reserved("a", 1, "terminal")
    now[0] = 11
    assert storage.get("a") is None


def test_legacy_updates_are_atomic_across_threads(storage):
    storage.setex("a", 20, "0")

    def increment(_):
        storage.update("a", 20, lambda value: str(int(value) + 1))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(100)))
    assert storage.get("a") == "100"


@pytest.mark.asyncio
async def test_expiration_and_unknown_lookup_clean_lock_registry(storage):
    now = [0.0]
    storage._clock = lambda: now[0]
    async with storage.serialize("unknown"):
        pass
    assert storage._async_locks == {}
    storage.setex("a", 1, "one")
    async with storage.serialize("a"):
        assert isinstance(storage._async_locks["a"][0], asyncio.Lock)
    now[0] = 1
    storage._cleanup_expired()
    assert storage._store == {}
    assert storage._async_locks == {}
