"""Bounded programmatic generation CLI; stdout contains a single terminal result."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import stat
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from typing import Any

from clink.allowed_roots import (
    Enrollment,
    EnrollmentError,
    allow_command,
    approved,
    canonical_directory,
    prepare_enrollment,
)
from clink.contracts import BackendError
from clink.generation import GenerationService
from clink.generation_contracts import (
    RAW_REQUEST_BYTES,
    RESULT_BYTES,
    STDIN_SECONDS,
    GenerationInputError,
    canonical_json,
    parse_request,
    result_exit_code,
    terminal_result,
)
from clink.native_generation import NativeGenerationService
from clink.native_generation_contracts import parse_native_request, terminal_native_result
from clink.registry import ClinkRegistry, RegistryLoadError

_APPROVAL_CANCELLED: ContextVar[asyncio.Event | None] = ContextVar("clink_approval_cancelled", default=None)


class _EnrollmentDeclined(Exception):
    pass


def _approval_guard(deadline: float | None = None) -> Callable[[], None]:
    parent_pid = os.getppid()
    cancelled = _APPROVAL_CANCELLED.get()

    def check() -> None:
        if os.getppid() != parent_pid or (cancelled is not None and cancelled.is_set()):
            raise asyncio.CancelledError
        if deadline is not None and time.monotonic() >= deadline:
            raise BackendError("timeout", "The generation deadline expired during directory approval.")

    return check


async def _terminal_answer(fd: int) -> bytes:
    answer = bytearray()
    while len(answer) <= 1024:
        try:
            chunk = os.read(fd, 256)
        except BlockingIOError:
            await asyncio.sleep(0.05)
            continue
        if not chunk:
            return b""
        answer.extend(chunk)
        if b"\n" in answer:
            return bytes(answer).split(b"\n", 1)[0]
    return b""


async def confirm_enrollment(
    plan: Enrollment, *, yes: bool = False, deadline: float | None = None, standalone: bool = False
) -> bool:
    """Ask once on a separate foreground terminal; never consume request stdin."""
    if plan.already_allowed:
        return True
    if plan.broad:
        print(f"Warning: {plan.entry} grants access to a broad directory and all its descendants.", file=sys.stderr)
    if yes:
        return True
    remedy = allow_command(
        str(plan.target), plan.cli_name, plan.registry.project_dir, plan.registry.consumer_config_dir
    )
    descriptor = None
    try:
        if not sys.stderr.isatty() or os.environ.get("CI") or (standalone and not sys.stdin.isatty()):
            raise OSError
        descriptor = os.open("/dev/tty", os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
        if os.tcgetpgrp(descriptor) != os.getpgrp():
            raise OSError
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise EnrollmentError(f"Terminal approval is unavailable. Preapprove with: {remedy}") from None
    try:
        print(
            f"{plan.entry} is not in the allowed paths for {plan.cli_name}. "
            "Allow Clink to use this folder from now on? [y/N] ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
        answer = await asyncio.wait_for(_terminal_answer(descriptor), remaining)
        return answer.strip().lower() in {b"y", b"yes"}
    finally:
        os.close(descriptor)


async def prepare_native_generation(request: Any, registry: ClinkRegistry | None) -> ClinkRegistry:
    guard = _approval_guard(request.deadline)
    registry = registry if registry is not None else ClinkRegistry()
    if registry.project_dir is not None:
        raise BackendError("invalid_config", "Native generation requires independent user or explicit configuration.")
    if approved(registry, request.cli, Path(request.project_dir), native=True):
        guard()
        return registry
    plan = prepare_enrollment(registry, request.cli, request.project_dir, native=True)
    if not await confirm_enrollment(plan, deadline=request.deadline):
        raise _EnrollmentDeclined
    if time.monotonic() >= request.deadline:
        raise asyncio.TimeoutError
    await asyncio.sleep(0)
    refreshed = plan.persist(guard=guard)
    print(f"Added {plan.entry} to allowed paths in {json.dumps(str(plan.destination))}.", file=sys.stderr)
    guard()
    await asyncio.sleep(0)
    guard()
    return refreshed


def _allow_result(args: argparse.Namespace, target: str, status: str, saved: bool, code: str | None) -> None:
    if getattr(args, "json", False):
        result = {
            "schema_version": 1,
            "cli": args.cli,
            "project_dir": target,
            "status": status,
            "grant_saved": saved,
            "error_code": code,
        }
        encoded = canonical_json(result)
        if len(encoded) > RESULT_BYTES:
            raise EnrollmentError("Directory approval result exceeds the output limit.", grant_saved=saved)
        sys.stdout.write(encoded.decode("utf-8") + "\n")
        sys.stdout.flush()


async def _allow(args: argparse.Namespace) -> int:
    guard = _approval_guard()
    target = args.path
    saved = False
    publishing = False
    consumer = getattr(args, "consumer_config_dir", None)
    project = Path(args.project) if args.project else None

    def mark_saved() -> None:
        nonlocal saved
        saved = True

    try:
        target = str(canonical_directory(args.path))
        registry = ClinkRegistry(project_dir=project, consumer_config_dir=consumer)
        plan = prepare_enrollment(registry, args.cli, args.path)
        target = str(plan.target)
        if not await confirm_enrollment(plan, yes=args.yes, standalone=True):
            print("Nothing was linked.", file=sys.stderr)
            _allow_result(args, target, "failed", False, "cancelled")
            return 0
        if plan.already_allowed:
            saved = True
            status = "already_allowed"
            print(f"Already allowed: {plan.entry}", file=sys.stderr)
        else:
            await asyncio.sleep(0)
            publishing = True
            plan.persist(guard=guard, published=mark_saved)
            guard()
            status = "allowed"
            print(
                f"Added {plan.entry} to allowed paths in {json.dumps(str(plan.destination))}. "
                "Restart an existing MCP server to reload.",
                file=sys.stderr,
            )
        _allow_result(args, target, status, saved, None)
        return 0
    except (EnrollmentError, RegistryLoadError, KeyError) as exc:
        known = exc.grant_saved if isinstance(exc, EnrollmentError) else (True if saved else False)
        print(
            f"clink allow: {exc}; allowed_roots entry {json.dumps(target)}. "
            f"Operator command: {allow_command(target, args.cli, project, consumer)}",
            file=sys.stderr,
        )
        if known is not None:
            _allow_result(
                args,
                target,
                "failed",
                saved or known,
                exc.error_code if isinstance(exc, EnrollmentError) else "invalid_config",
            )
        return 1
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        print("Directory approval was cancelled; inspect configuration before retrying.", file=sys.stderr)
        # Publication is synchronous and its callback runs before fsync/reload. If an
        # unexpected publisher cannot establish its outcome, omit the envelope.
        known = getattr(exc, "grant_saved", None)
        if saved or not publishing or known is not None:
            _allow_result(args, target, "failed", saved or bool(known), "cancelled")
        return 1
    except Exception:
        print(
            "clink allow: Enrollment failed internally. Inspect operator configuration before retrying.",
            file=sys.stderr,
        )
        if saved or not publishing:
            _allow_result(args, target, "failed", saved, "internal_error")
        return 1


async def read_request_fd(fd: int) -> bytes:
    """Read one bounded body without a blocking worker thread that can outlive cancellation."""
    loop = asyncio.get_running_loop()
    regular = stat.S_ISREG(os.fstat(fd).st_mode)
    was_blocking = os.get_blocking(fd)
    os.set_blocking(fd, False)
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            if not regular:
                ready = loop.create_future()

                def readable(future: asyncio.Future[None] = ready) -> None:
                    if not future.done():
                        future.set_result(None)

                loop.add_reader(fd, readable)
                try:
                    await ready
                finally:
                    loop.remove_reader(fd)
            try:
                chunk = os.read(fd, min(65536, RAW_REQUEST_BYTES + 1 - size))
            except BlockingIOError:
                continue
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > RAW_REQUEST_BYTES:
                raise GenerationInputError("input_limit", "The raw request exceeds the 1 MiB byte limit.")
            chunks.append(chunk)
            if regular:
                await asyncio.sleep(0)
    finally:
        os.set_blocking(fd, was_blocking)


async def _generate(started_at: float, fd: int, registry: ClinkRegistry | None = None) -> dict[str, Any]:
    service: GenerationService | NativeGenerationService | None = None
    request = None
    make_result = terminal_result
    try:
        raw = await asyncio.wait_for(read_request_fd(fd), max(0, started_at + STDIN_SECONDS - time.monotonic()))
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            envelope = None
        if (
            isinstance(envelope, dict)
            and type(envelope.get("schema_version")) is int
            and envelope["schema_version"] in (2, 3)
        ):
            make_result = partial(terminal_native_result, schema_version=envelope["schema_version"])
            request = parse_native_request(raw, started_at=started_at)
            if os.environ.get("CLINK_DELEGATION_DEPTH", "0") != "0":
                raise BackendError("recursion_blocked", "Nested native generation delegation is prohibited.")
            service = NativeGenerationService()
            if registry is not None:
                service.registry = registry
            service.registry = await prepare_native_generation(request, service.registry)
        else:
            request = parse_request(raw, started_at=started_at)
            service = GenerationService()
            if registry is not None:
                service.registry = registry
        result = await service.run(request)
    except asyncio.TimeoutError:
        result = make_result(
            started_at=started_at,
            code="timeout",
            status="timed_out",
            request=request,
            message=(
                "The generation deadline expired during directory approval."
                if request is not None
                else "Standard input did not reach EOF within ten seconds."
            ),
        )
    except GenerationInputError as exc:
        result = make_result(started_at=started_at, request_id=exc.request_id, code=exc.code, message=str(exc))
    except BackendError as exc:
        result = make_result(
            started_at=started_at,
            request=request,
            code=exc.code,
            message=str(exc),
            status="timed_out" if exc.code == "timeout" else "failed",
        )
    except (EnrollmentError, RegistryLoadError, KeyError) as exc:
        entry = json.dumps(request.project_dir) if request is not None else "unavailable"
        remedy = (
            allow_command(
                request.project_dir,
                request.cli,
                consumer_config_dir=registry.consumer_config_dir if registry is not None else None,
            )
            if request is not None
            else "clink allow PATH --cli NAME --yes"
        )
        print(f"clink generate: {exc}; allowed_roots entry {entry}. Operator command: {remedy}", file=sys.stderr)
        message = f"Directory approval is required. Preapprove with: {remedy}"
        if len(message) > 1024:
            message = (
                f"Directory approval is required. Run clink allow <project_dir> --cli {request.cli} --yes "
                "using the canonical request project_dir. See stderr for the complete command."
            )
        if isinstance(exc, EnrollmentError) and exc.grant_saved:
            message = (
                "The directory grant was saved, but generation did not start. "
                "Inspect operator configuration and stderr before retrying."
            )
        result = make_result(
            started_at=started_at,
            request=request,
            code="path_not_allowed",
            message=message,
        )
    except _EnrollmentDeclined:
        result = make_result(
            started_at=started_at,
            request=request,
            status="cancelled",
            code="cancelled",
            message="Nothing was linked.",
        )
    except asyncio.CancelledError:
        result = make_result(
            started_at=started_at,
            request=request,
            status="cancelled",
            code="cancelled",
            message="The generation attempt was cancelled after controlled cleanup.",
        )
    except Exception:
        result = make_result(
            started_at=started_at, request=request, code="internal_error", message="Generation failed internally."
        )
    finally:
        if service is not None:
            # The pool already shields child cleanup; finish shutdown before publishing a result.
            shutdown = asyncio.create_task(service.shutdown())
            while not shutdown.done():
                try:
                    await asyncio.shield(shutdown)
                except asyncio.CancelledError:
                    continue
            await shutdown
    result["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    return result


async def run_generate(
    started_at: float, parent_pid: int, fd: int, registry: ClinkRegistry | None = None
) -> dict[str, Any]:
    return await _supervise(_generate(started_at, fd, registry), parent_pid, started_at=started_at)


async def _supervise(operation: Any, parent_pid: int, *, started_at: float | None = None) -> Any:
    loop = asyncio.get_running_loop()
    cancelled = asyncio.Event()
    cancellation_token = _APPROVAL_CANCELLED.set(cancelled)
    attempt = asyncio.create_task(operation)

    def cancel() -> None:
        cancelled.set()
        if not attempt.done():
            attempt.cancel()

    async def watch_parent() -> None:
        while not attempt.done():
            if os.getppid() != parent_pid:
                cancel()
                return
            await asyncio.sleep(0.1)

    installed = []
    watcher = asyncio.create_task(watch_parent())
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(sig)

            def cancel_signal(_number: int, _frame: Any) -> None:
                cancel()
                loop.call_soon_threadsafe(lambda: None)

            signal.signal(sig, cancel_signal)
            installed.append((sig, previous))
        try:
            return await attempt
        except asyncio.CancelledError:
            if started_at is None:
                print("Directory approval was cancelled; inspect configuration before retrying.", file=sys.stderr)
                return 1
            return terminal_result(
                started_at=started_at,
                status="cancelled",
                code="cancelled",
                message="The generation attempt was cancelled before submission.",
            )
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        for sig, previous in installed:
            signal.signal(sig, previous)
        _APPROVAL_CANCELLED.reset(cancellation_token)


class _InvocationError(GenerationInputError):
    def __init__(self, allow_args: argparse.Namespace | None) -> None:
        super().__init__(
            "invalid_invocation",
            "Use clink generate --request-json - or clink allow PATH --cli NAME [--project PATH] [--yes] [--json].",
        )
        self.allow_args = allow_args


class _ArgumentParser(argparse.ArgumentParser):
    _error_namespace: argparse.Namespace | None = None

    def parse_known_args(
        self, args: list[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        namespace = namespace if namespace is not None else argparse.Namespace()
        self._error_namespace = namespace
        return super().parse_known_args(args, namespace)

    def error(self, _message: str) -> None:
        values = vars(self._error_namespace) if self._error_namespace is not None else {}
        identifiable = (
            values.get("command") == "allow"
            and values.get("json") is True
            and isinstance(values.get("path"), str)
            and isinstance(values.get("cli"), str)
        )
        raise _InvocationError(self._error_namespace if identifiable else None)


def main(argv: list[str] | None = None) -> int:
    started_at = time.monotonic()
    parent_pid = os.getppid()
    parser = _ArgumentParser(prog="clink", description="One bounded text-generation attempt.")
    parser.add_argument(
        "--consumer-config-dir", help="Bind an independent generation-only canonical configuration directory."
    )
    subcommands = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)
    generate = subcommands.add_parser(
        "generate", help="Read one v1 or explicit native v2/v3 generation request from stdin."
    )
    generate.add_argument("--request-json", required=True, choices=("-",))
    generate.add_argument("--consumer-config-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    allow = subcommands.add_parser("allow", help="Approve and remember a directory for an operator-configured CLI.")
    allow.set_defaults(command="allow")
    allow.add_argument("path")
    allow.add_argument("--cli", required=True)
    allow.add_argument("--project", help="Explicit server project binding to reproduce.")
    allow.add_argument("--consumer-config-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    allow.add_argument("--json", action="store_true", help="Write one schema-one directory grant result to stdout.")
    allow.add_argument("--yes", action="store_true", help="Explicitly preapprove without a terminal question.")
    capabilities = subcommands.add_parser(
        "generation-capabilities", help="Read generation admission without invoking a CLI."
    )
    capabilities.add_argument("--json", required=True, action="store_true")
    capabilities.add_argument("--cli", choices=("codex", "claude", "agy", "grok"))
    capabilities.add_argument("--executable", help="Read one absolute executable identity without starting it.")
    try:
        args = parser.parse_args(argv)
        if args.command == "generation-capabilities":
            from clink.generation_capabilities import generation_capabilities

            try:
                result = asyncio.run(_supervise(generation_capabilities(args.cli, args.executable), parent_pid))
                if not isinstance(result, dict):
                    return 1
                encoded = canonical_json(result) + b"\n"
                if len(encoded) > 65536:
                    raise ValueError
                sys.stdout.buffer.write(encoded)
                sys.stdout.buffer.flush()
                return 0
            except (ValueError, OSError, KeyboardInterrupt, asyncio.CancelledError):
                print("clink generation-capabilities: Invalid or interrupted read-only preflight.", file=sys.stderr)
                return 2
        if args.command == "allow" and args.consumer_config_dir is not None and args.project is not None:
            if args.json:
                try:
                    target = str(canonical_directory(args.path))
                except EnrollmentError:
                    target = args.path
                _allow_result(args, target, "failed", False, "invalid_invocation")
                print("clink allow: --consumer-config-dir and --project are mutually exclusive.", file=sys.stderr)
                return 1
            parser.error("--consumer-config-dir and --project are mutually exclusive")
        if args.command == "allow":
            try:
                return asyncio.run(_supervise(_allow(args), parent_pid))
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("Directory approval was cancelled; inspect configuration before retrying.", file=sys.stderr)
                return 1
            except Exception:
                print(
                    "clink allow: Enrollment failed internally. Inspect operator configuration before retrying.",
                    file=sys.stderr,
                )
                return 1
        # Bind before touching stdin. Broken consumer configuration must never wait
        # on an open request pipe or fall back to unrelated operator configuration.
        registry = (
            ClinkRegistry(consumer_config_dir=args.consumer_config_dir)
            if args.consumer_config_dir is not None
            else None
        )
        result = asyncio.run(run_generate(started_at, parent_pid, sys.stdin.fileno(), registry))
    except GenerationInputError as exc:
        if isinstance(exc, _InvocationError) and exc.allow_args is not None:
            try:
                target = str(canonical_directory(exc.allow_args.path))
            except EnrollmentError:
                target = exc.allow_args.path
            _allow_result(exc.allow_args, target, "failed", False, exc.code)
            print(f"clink allow: {exc}", file=sys.stderr)
            return 1
        result = terminal_result(started_at=started_at, code=exc.code, message=str(exc))
    except RegistryLoadError as exc:
        print(f"clink generate: {exc}", file=sys.stderr)
        result = terminal_result(started_at=started_at, code="invalid_config", message=str(exc)[:1024])
    except KeyboardInterrupt:
        result = terminal_result(
            started_at=started_at, status="cancelled", code="cancelled", message="Generation was cancelled."
        )
    except Exception:
        result = terminal_result(started_at=started_at, code="internal_error", message="Generation failed internally.")
    try:
        # Publication follows private resource, pool and event-loop shutdown.
        result["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
        encoded = canonical_json(result) + b"\n"
        if len(encoded) > RESULT_BYTES:
            make_result = (
                partial(terminal_native_result, schema_version=result["schema_version"])
                if result["schema_version"] in (2, 3)
                else terminal_result
            )
            result = make_result(
                started_at=started_at, code="output_limit", message="The serialized result exceeds its limit."
            )
            encoded = canonical_json(result) + b"\n"
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        # All owned work is already cleaned up before writing to the possibly absent caller.
        with contextlib.suppress(OSError):
            # Python may retry a buffered flush during interpreter shutdown.
            # Keep that retry away from the absent caller and preserve our exit code.
            discard = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(discard, sys.stdout.fileno())
            finally:
                os.close(discard)
        return 1
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
