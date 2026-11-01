"""Bounded POSIX process supervision shared by every CLI entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from clink.contracts import BackendError

logger = logging.getLogger("clink.runner")
STREAM_BYTES = 10 * 1024 * 1024
QUEUE_LIMIT = 8
QUEUE_SECONDS = 30.0
CLEANUP_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessOutput:
    """Ephemeral streams; callers must never persist these in normal metadata."""

    stdout: str
    stderr: str
    returncode: int
    stdout_bytes: int
    stderr_bytes: int
    queue_seconds: float


class ProcessPool:
    """A bounded FIFO launch pool with cancellation-safe permit ownership."""

    def __init__(self, maximum: int = 2):
        if type(maximum) is not int or maximum < 1:
            raise BackendError("invalid_config", "CLINK_MAX_CONCURRENT must be a positive integer.")
        self.maximum = maximum
        self.running = 0
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._children: set[asyncio.subprocess.Process] = set()
        self._closed = False
        self._executions: set[asyncio.Task[Any]] = set()

    async def _acquire(self, deadline: float) -> float:
        start = time.monotonic()
        if self._closed:
            raise BackendError("cancelled", "The CLI backend is shutting down.")
        if start >= deadline:
            raise BackendError("timeout", "The request deadline expired before launch.")
        if self.running < self.maximum and not self._waiters:
            self.running += 1
            return 0.0
        if len(self._waiters) >= QUEUE_LIMIT:
            raise BackendError("queue_full", "The delegated-process queue is full.", "Submit a new request later.")
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        returned = False
        try:
            limit = min(QUEUE_SECONDS, deadline - start)
            await asyncio.wait_for(asyncio.shield(waiter), limit)
            if self._closed:
                raise BackendError("cancelled", "The CLI backend is shutting down.")
            if time.monotonic() >= deadline:
                raise BackendError("timeout", "The request deadline expired while queued.")
            elapsed = time.monotonic() - start
            returned = True
            return elapsed
        except asyncio.TimeoutError as exc:
            code = "timeout" if time.monotonic() >= deadline else "queue_timeout"
            raise BackendError(code, "The delegated-process queue wait expired.") from exc
        finally:
            if not returned:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                    waiter.cancel()
                elif waiter.done() and not waiter.cancelled() and waiter.exception() is None:
                    # Cancellation can race a release that already transferred ownership.
                    self._release()

    def _release(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
        self.running -= 1

    @staticmethod
    async def _read(stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > STREAM_BYTES:
                raise BackendError("output_limit", "A CLI output stream exceeded the 10 MiB byte limit.")
            chunks.append(chunk)

    @staticmethod
    async def _write(stream: asyncio.StreamWriter, content: bytes) -> None:
        try:
            stream.write(content)
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            # A child that exits before consuming stdin is still classified by exit/envelope.
            pass
        finally:
            stream.close()

    @staticmethod
    def _signal_group(process: asyncio.subprocess.Process, sig: int) -> bool:
        try:
            os.killpg(process.pid, sig)
            return True
        except ProcessLookupError:
            return False

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        if self._signal_group(process, signal.SIGTERM):
            # A dead group leader can leave descendants alive; inspect the group, not returncode.
            until = time.monotonic() + CLEANUP_SECONDS
            while time.monotonic() < until:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.05)
            self._signal_group(process, signal.SIGKILL)
        await process.wait()

    async def execute(self, *args: Any, **kwargs: Any) -> ProcessOutput:
        task = asyncio.current_task()
        assert task is not None
        self._executions.add(task)
        try:
            return await self._execute(*args, **kwargs)
        finally:
            self._executions.discard(task)

    async def _execute(
        self,
        command: tuple[str, ...],
        *,
        content: bytes,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        request_id: str,
        metadata: dict[str, Any] | None = None,
        prelaunch: Callable[[], None] | None = None,
    ) -> ProcessOutput:
        if os.name != "posix":
            raise BackendError("unsupported_platform", "Delegation requires verified POSIX process-group cleanup.")
        queue_seconds = await self._acquire(deadline)
        process = None
        tasks: list[asyncio.Task[Any]] = []
        start = time.monotonic()
        outcome = "cancelled"
        safe_metadata = {
            key: value if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value) else "unknown"
            for key, value in (metadata or {}).items()
            if key in {"client", "role", "tool"}
        }
        try:
            if self._closed:
                raise BackendError("cancelled", "The CLI backend is shutting down.")
            if start >= deadline:
                raise BackendError("timeout", "The request deadline expired before launch.")
            if prelaunch is not None:
                prelaunch()
            if time.monotonic() >= deadline:
                raise BackendError("timeout", "The request deadline expired during launch admission checks.")
            if self._closed:
                raise BackendError("cancelled", "The CLI backend is shutting down.")
            try:
                spawn = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *command,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                        env=env,
                        start_new_session=True,
                        limit=65536,
                    )
                )
                try:
                    process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # The OS spawn may already have happened. Finish acquiring its handle before cleanup.
                    while not spawn.done():
                        try:
                            await asyncio.shield(spawn)
                        except asyncio.CancelledError:
                            continue
                    process = spawn.result()
                    self._children.add(process)
                    raise
            except FileNotFoundError as exc:
                raise BackendError("cli_missing", "The admitted CLI executable is unavailable.") from exc
            except (PermissionError, OSError) as exc:
                raise BackendError("invalid_config", "The CLI executable could not be launched.") from exc
            self._children.add(process)
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None
            tasks = [
                asyncio.create_task(self._read(process.stdout)),
                asyncio.create_task(self._read(process.stderr)),
                asyncio.create_task(self._write(process.stdin, content)),
                asyncio.create_task(process.wait()),
            ]
            try:
                stdout, stderr, _, returncode = await asyncio.wait_for(
                    asyncio.gather(*tasks), max(0.0, deadline - time.monotonic())
                )
            except asyncio.TimeoutError as exc:
                raise BackendError("timeout", "The delegated request deadline expired.") from exc
            outcome = "completed" if returncode == 0 else "nonzero_exit"
            try:
                stdout_text = stdout.decode("utf-8")
            except UnicodeDecodeError:
                raise BackendError("parse_error", "The CLI output envelope was not valid UTF-8.") from None
            return ProcessOutput(
                stdout_text,
                stderr.decode("utf-8", errors="replace"),
                returncode,
                len(stdout),
                len(stderr),
                queue_seconds,
            )
        except BackendError as exc:
            outcome = exc.code
            raise
        finally:

            async def finalize() -> None:
                try:
                    if process is not None:
                        await self._cleanup(process)
                finally:
                    if process is not None:
                        self._children.discard(process)
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

            # Repeated caller cancellation cannot interrupt process-tree cleanup or leak a permit.
            finalizer = asyncio.create_task(finalize())
            interrupted = False
            try:
                while True:
                    try:
                        await asyncio.shield(finalizer)
                        break
                    except asyncio.CancelledError:
                        interrupted = True
                        if finalizer.done():
                            raise
            finally:
                self._release()
                logger.info(
                    "cli_process request_id=%s outcome=%s duration_seconds=%.3f queue_seconds=%.3f client=%s role=%s tool=%s",
                    request_id,
                    outcome,
                    time.monotonic() - start,
                    queue_seconds,
                    safe_metadata.get("client", "unknown"),
                    safe_metadata.get("role", "unknown"),
                    safe_metadata.get("tool", "unknown"),
                )
            if interrupted:
                raise asyncio.CancelledError

    async def shutdown(self) -> None:
        """Stop admission, cancel all queued/spawning/running owners, and await cleanup."""
        self._closed = True
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_exception(BackendError("cancelled", "The CLI backend is shutting down."))
        self._waiters.clear()
        current = asyncio.current_task()
        owners = tuple(task for task in self._executions if task is not current)
        for task in owners:
            task.cancel()
        if owners:
            await asyncio.gather(*owners, return_exceptions=True)
        if self._children:
            await asyncio.gather(*(self._cleanup(child) for child in tuple(self._children)), return_exceptions=True)
