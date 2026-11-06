"""Small native transport/parser boundary; shared admission and process limits stay central."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from clink.contracts import BackendError
from clink.native_contracts import FrozenNativeContext, ParsedNativeResponse
from clink.native_policy import NATIVE_PROMPT_FILE, native_arguments

PROMPT_BYTES = 1024 * 1024


class NativeAdapter(ABC):
    """Own argv, session validation and parsing for one documented native interface."""

    name: str
    prompt_channel: str
    permission_handling: str

    @abstractmethod
    def command(
        self, context: FrozenNativeContext, session_id: str | None, prompt_path: str | None = None
    ) -> tuple[str, ...]:
        """Build protected direct argv without embedding task text."""

    @abstractmethod
    def validate_session(self, session_id: str | None) -> str | None:
        """Reject identities that are ambiguous or unsafe for native resume."""

    @abstractmethod
    def parse(self, stdout: str, stderr: str) -> ParsedNativeResponse:
        """Return bounded claims and a separate private native session identity."""


class ClaudeNativeAdapter(NativeAdapter):
    name = "claude"
    prompt_channel = "stdin_text"
    permission_handling = "native policy; unattended prompts denied and reported"

    def validate_session(self, session_id: str | None) -> str | None:
        from clink.parsers.claude_native import validate_session_id

        return validate_session_id(session_id)

    def command(
        self, context: FrozenNativeContext, session_id: str | None, prompt_path: str | None = None
    ) -> tuple[str, ...]:
        self.validate_session(session_id)
        target = context.target
        command = [*target.client.executable, *native_arguments(self.name), *target.client.config_args]
        if target.effective_model is not None:
            command.extend(("--model", target.effective_model))
        if session_id is not None:
            command.extend(("--resume", session_id))
        return tuple(command)

    def parse(self, stdout: str, stderr: str) -> ParsedNativeResponse:
        from clink.parsers.claude_native import ClaudeNativeParser

        return ClaudeNativeParser().parse(stdout, stderr)


class GrokNativeAdapter(NativeAdapter):
    name = "grok"
    prompt_channel = "private_file"
    permission_handling = "native policy; headless permission requests refused and reported"

    def validate_session(self, session_id: str | None) -> str | None:
        from clink.parsers.grok_native import validate_session_id

        return validate_session_id(session_id)

    def command(
        self, context: FrozenNativeContext, session_id: str | None, prompt_path: str | None = None
    ) -> tuple[str, ...]:
        self.validate_session(session_id)
        if prompt_path is None:
            raise BackendError("invalid_config", "Grok native execution requires a private prompt file.")
        target = context.target
        arguments = tuple(prompt_path if item == NATIVE_PROMPT_FILE else item for item in native_arguments(self.name))
        command = [*target.client.executable, *arguments, *target.client.config_args]
        if target.effective_model is not None:
            command.extend(("--model", target.effective_model))
        if session_id is not None:
            command.extend(("--resume", session_id))
        return tuple(command)

    def parse(self, stdout: str, stderr: str) -> ParsedNativeResponse:
        from clink.parsers.grok_native import GrokNativeParser

        return GrokNativeParser().parse(stdout, stderr)


class AgyNativeAdapter(NativeAdapter):
    name = "agy"
    prompt_channel = "stdin_user_ndjson"
    permission_handling = "native policy; headless confirmation requests soft-denied and reported"

    def validate_session(self, session_id: str | None) -> str | None:
        from clink.parsers.agy_native import validate_session_id

        return validate_session_id(session_id)

    def command(
        self, context: FrozenNativeContext, session_id: str | None, prompt_path: str | None = None
    ) -> tuple[str, ...]:
        self.validate_session(session_id)
        target = context.target
        command = [*target.client.executable, *native_arguments(self.name), *target.client.config_args]
        if target.effective_model is not None:
            command.extend(("--model", target.effective_model))
        if session_id is not None:
            command.extend(("--conversation", session_id))
        return tuple(command)

    def parse(self, stdout: str, stderr: str) -> ParsedNativeResponse:
        from clink.parsers.agy_native import AgyNativeParser

        return AgyNativeParser().parse(stdout, stderr)


class CodexNativeAdapter(NativeAdapter):
    name = "codex"
    prompt_channel = "stdin_text"
    permission_handling = "native exec approval semantics; interactive approvals unavailable"

    def validate_session(self, session_id: str | None) -> str | None:
        from clink.parsers.codex_native import validate_session_id

        return validate_session_id(session_id)

    def command(
        self, context: FrozenNativeContext, session_id: str | None, prompt_path: str | None = None
    ) -> tuple[str, ...]:
        self.validate_session(session_id)
        target = context.target
        if session_id is None:
            arguments = [*native_arguments(self.name), "--cd", str(target.working_directory)]
        else:
            arguments = ["exec", "--cd", str(target.working_directory), "resume", "--json"]
        command = [*target.client.executable, *arguments, *target.client.config_args]
        if target.effective_model is not None:
            command.extend(("--model", target.effective_model))
        if session_id is not None:
            command.append(session_id)
        command.append("-")
        return tuple(command)

    def parse(self, stdout: str, stderr: str) -> ParsedNativeResponse:
        from clink.parsers.codex_native import CodexNativeParser

        return CodexNativeParser().parse(stdout, stderr)


_ADAPTERS: dict[str, NativeAdapter] = {
    adapter.name: adapter
    for adapter in (ClaudeNativeAdapter(), GrokNativeAdapter(), AgyNativeAdapter(), CodexNativeAdapter())
}


def get_native_adapter(name: str) -> NativeAdapter:
    if name not in _ADAPTERS:
        raise BackendError("unsupported_capability", "This adapter has no native task contract.")
    return _ADAPTERS[name]


@dataclass(frozen=True)
class NativeLaunch:
    """Private launch resources remain alive until shared process cleanup completes."""

    command: tuple[str, ...]
    stdin: bytes
    validate_input: Callable[[], None]


@contextmanager
def prepare_native_launch(
    adapter: NativeAdapter, context: FrozenNativeContext, content: bytes, session_id: str | None
) -> Iterator[NativeLaunch]:
    """Keep task text off argv; dispose private prompt files on every exit path."""
    if len(content) > PROMPT_BYTES:
        raise BackendError("input_limit", "The native input exceeds the 1 MiB byte limit.")
    adapter.validate_session(session_id)
    if adapter.prompt_channel == "private_file":
        with tempfile.TemporaryDirectory(prefix="clink-native-input-") as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            path = root / "prompt.txt"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
            parent_identity = root.stat()
            file_identity = path.stat()
            digest = hashlib.sha256(content).digest()

            def validate_input() -> None:
                try:
                    parent = root.lstat()
                    if (
                        not stat.S_ISDIR(parent.st_mode)
                        or stat.S_IMODE(parent.st_mode) != 0o700
                        or (parent.st_dev, parent.st_ino) != (parent_identity.st_dev, parent_identity.st_ino)
                    ):
                        raise OSError
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
                    with os.fdopen(descriptor, "rb") as stream:
                        observed = os.fstat(stream.fileno())
                        if (
                            not stat.S_ISREG(observed.st_mode)
                            or stat.S_IMODE(observed.st_mode) != 0o600
                            or (observed.st_dev, observed.st_ino, observed.st_size)
                            != (file_identity.st_dev, file_identity.st_ino, len(content))
                        ):
                            raise OSError
                        data = stream.read(PROMPT_BYTES + 1)
                    if hashlib.sha256(data).digest() != digest:
                        raise OSError
                except OSError:
                    raise BackendError(
                        "invalid_config", "The private native input is unavailable or changed."
                    ) from None

            yield NativeLaunch(adapter.command(context, session_id, str(path)), b"", validate_input)
        return
    if adapter.prompt_channel == "stdin_user_ndjson":
        stdin = (
            json.dumps({"event": "user", "message": {"content": content.decode("utf-8")}}, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if len(stdin) > PROMPT_BYTES:
            raise BackendError("input_limit", "The native input envelope exceeds the 1 MiB byte limit.")
    else:
        stdin = content
    yield NativeLaunch(adapter.command(context, session_id), stdin, lambda: None)
