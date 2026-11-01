"""Bounded, process-local conversation storage with atomic admission reservations."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

MAX_ACTIVE_THREADS = 256
MAX_STORAGE_BYTES = 64 * 1024 * 1024


class StorageCapacityError(RuntimeError):
    """The process cannot admit another record without evicting live state."""


class InMemoryStorage:
    """Keep expiring JSON records; never evict a live conversation for capacity."""

    def __init__(
        self,
        max_threads: int = MAX_ACTIVE_THREADS,
        max_bytes: int = MAX_STORAGE_BYTES,
        clock: Callable[[], float] = time.time,
    ):
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()
        self._reservations: dict[str, int] = {}
        self._async_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self.max_threads = max_threads
        self.max_bytes = max_bytes
        self._clock = clock
        self._stop = threading.Event()
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()

    @staticmethod
    def _size(value: str) -> int:
        return len(value.encode("utf-8"))

    def _cleanup_expired_locked(self) -> None:
        now = self._clock()
        for key, (_, expires_at) in list(self._store.items()):
            # A reserved operation owns its record until its terminal update.
            if expires_at <= now and key not in self._reservations:
                del self._store[key]
                self._discard_idle_lock(key)

    def _discard_idle_lock(self, key: str) -> None:
        entry = self._async_locks.get(key)
        if entry is not None and entry[1] == 0 and key not in self._store:
            del self._async_locks[key]

    def _check_capacity(self, key: str, value: str, reservation: int = 0) -> None:
        old_size = self._size(self._store[key][0]) if key in self._store else 0
        total = sum(self._size(item[0]) for item in self._store.values()) - old_size + self._size(value)
        total += sum(size for reserved_key, size in self._reservations.items() if reserved_key != key)
        if key not in self._store and len(self._store) >= self.max_threads:
            raise StorageCapacityError("Conversation capacity reached; wait for an existing conversation to expire.")
        if total + reservation > self.max_bytes:
            raise StorageCapacityError("Conversation storage is full; wait for existing conversations to expire.")

    def set_with_ttl(self, key: str, ttl_seconds: int, value: str) -> None:
        """Atomically admit a record, respecting every outstanding reservation."""
        with self._lock:
            self._cleanup_expired_locked()
            if key in self._reservations:
                raise RuntimeError("Conversation is owned by an active transaction")
            self._check_capacity(key, value)
            self._store[key] = (value, self._clock() + ttl_seconds)

    def get(self, key: str) -> str | None:
        with self._lock:
            self._cleanup_expired_locked()
            entry = self._store.get(key)
            return entry[0] if entry is not None else None

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        self.set_with_ttl(key, ttl_seconds, value)

    def update(self, key: str, ttl_seconds: int, transform: Callable[[str], str]) -> bool:
        """Apply a synchronous legacy helper update without losing concurrent turns."""
        with self._lock:
            current = self.get(key)
            if current is None:
                return False
            self.setex(key, ttl_seconds, transform(current))
            return True

    def reserve(self, key: str, ttl_seconds: int, initial_value: str | None, extra_bytes: int) -> None:
        """Reserve a live slot and worst-case terminal growth before external work."""
        with self._lock:
            self._cleanup_expired_locked()
            existing = self._store.get(key)
            value = existing[0] if existing is not None else initial_value
            if value is None:
                raise KeyError(key)
            if key in self._reservations:
                raise RuntimeError("Conversation already has a reservation")
            self._check_capacity(key, value, extra_bytes)
            if existing is None:
                self._store[key] = (value, self._clock() + ttl_seconds)
            self._reservations[key] = extra_bytes

    def commit_reserved(self, key: str, ttl_seconds: int, value: str) -> None:
        """Publish a complete terminal record and release its growth reservation."""
        with self._lock:
            if key not in self._reservations or key not in self._store:
                raise RuntimeError("Conversation has no active reservation")
            previous_size = self._size(self._store[key][0])
            if self._size(value) > previous_size + self._reservations[key]:
                raise StorageCapacityError("Terminal conversation state exceeds its admitted storage reservation.")
            self._check_capacity(key, value)
            self._store[key] = (value, self._clock() + ttl_seconds)
            del self._reservations[key]

    def release_reservation(self, key: str) -> None:
        with self._lock:
            self._reservations.pop(key, None)
            self._cleanup_expired_locked()

    @asynccontextmanager
    async def serialize(self, key: str, deadline: float | None = None):
        """Serialize one continuation through its terminal update; include lock wait in deadline."""
        with self._lock:
            lock, users = self._async_locks.get(key, (asyncio.Lock(), 0))
            self._async_locks[key] = (lock, users + 1)
        acquired = False
        try:
            if deadline is None:
                await lock.acquire()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await asyncio.wait_for(lock.acquire(), remaining)
            acquired = True
            if deadline is not None and time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            yield
        finally:
            if acquired:
                lock.release()
            with self._lock:
                _, users = self._async_locks[key]
                self._async_locks[key] = (lock, users - 1)
                self._discard_idle_lock(key)

    def _cleanup_worker(self) -> None:
        while not self._stop.wait(300):
            self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        with self._lock:
            self._cleanup_expired_locked()

    def shutdown(self) -> None:
        self._stop.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=1)


_storage_instance = None
_storage_lock = threading.Lock()


def get_storage_backend() -> InMemoryStorage:
    """Return the process-local storage instance."""
    global _storage_instance
    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                _storage_instance = InMemoryStorage()
    return _storage_instance
