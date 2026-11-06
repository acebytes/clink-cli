"""Explicit, provider-free backend double for tool behavior tests."""

from types import SimpleNamespace

from clink.contracts import CLIResult


class RecordingBackend:
    """Capture immutable requests without launching a process or discovering login state."""

    def __init__(self, root):
        self.root = root.resolve()
        self.requests = []
        self.resolutions = []
        self.validations = []
        self.content = "A bounded advisory perspective."
        self.error = None

    def resolve(self, **arguments):
        self.resolutions.append(arguments)
        return SimpleNamespace(
            cli_name=arguments.get("cli_name") or "codex",
            client=SimpleNamespace(allowed_roots=(self.root,)),
        )

    def validate_files(self, target, files, images=(), tool_name="clink"):
        self.validations.append((tuple(files), tuple(images), tool_name))
        if self.error:
            raise self.error
        return tuple(files)

    async def run(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        selection = self.resolutions[-1]
        return CLIResult(
            content=self.content,
            status="success",
            request_id=request.request_id,
            cli_name=request.target.cli_name,
            role=selection.get("role") or "default",
            requested_model=selection.get("model"),
            actual_model=None,
            duration_seconds=0.01,
        )


def context_target(root):
    """Build a synthetic target for admission-only checks; this is not real CLI approval."""
    from clink.contracts import AdapterPolicy, BackendTarget, ClientSnapshot, RoleSnapshot

    return BackendTarget(
        client=ClientSnapshot("codex", "codex", ("synthetic-never-launched",), root, (root,)),
        role=RoleSnapshot("default", "Advice only"),
        requested_model=None,
        effective_model=None,
        working_directory=root,
        policy=AdapterPolicy("codex", version="fixture", verified=True, images=True),
    )
