"""Strict manifest models and frozen resolved clink configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _validate_name(value: str) -> str:
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError("must start with a letter and contain only letters, digits, underscores, or hyphens")
    return value


def _validate_model(value: str | None) -> str | None:
    if value is None or value == "auto":
        return None
    if (
        not value
        or value != value.strip()
        or value.startswith("-")
        or len(value) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("model must be a nonempty native identifier without control characters")
    return value


class OutputCaptureConfig(BaseModel):
    """Legacy import compatibility; generic output capture is not admitted by manifests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flag_template: str
    cleanup: bool = True


class CLIRoleConfig(BaseModel):
    """Operator-owned role manifest with no argument or type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prompt_path: str | None = None
    role_args: list[str] = Field(default_factory=list)
    description: str | None = None
    model: str | None = None

    _validate_native_model = field_validator("model")(_validate_model)


class NativeTaskConfig(BaseModel):
    """Opt-in task profile; inherited environment is configured by name only."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    inherit_env: list[str] = Field(default_factory=list, max_length=128)
    required_env: list[str] = Field(default_factory=list, max_length=128)
    roles: dict[str, CLIRoleConfig] = Field(default_factory=dict, max_length=64)
    default_model: str | None = None

    _validate_native_model = field_validator("default_model")(_validate_model)

    @field_validator("inherit_env", "required_env")
    @classmethod
    def _environment_names(cls, names: list[str]) -> list[str]:
        for name in names:
            if not _ENVIRONMENT_NAME.fullmatch(name) or name.startswith("CLINK_"):
                raise ValueError("native environment entries must be valid names outside the server namespace")
        if len(names) != len(set(names)):
            raise ValueError("native environment names must be unique")
        return names

    @field_validator("roles")
    @classmethod
    def _role_names(cls, roles: dict[str, CLIRoleConfig]) -> dict[str, CLIRoleConfig]:
        for name in roles:
            _validate_name(name)
        return roles

    @model_validator(mode="after")
    def _required_are_inherited(self) -> NativeTaskConfig:
        if not set(self.required_env).issubset(self.inherit_env):
            raise ValueError("required_env names must also appear in inherit_env")
        return self


class CLIClientConfig(BaseModel):
    """Raw version-one manifest; every recognized field is validated explicitly."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    name: str
    adapter: str | None = None
    command: str | None = None
    working_dir: str | None = None
    allowed_roots: list[str] | None = None
    additional_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0, le=1800)
    roles: dict[str, CLIRoleConfig] = Field(default_factory=dict)
    default_model: str | None = None
    native_task: NativeTaskConfig | None = None

    _validate_client_name = field_validator("name")(_validate_name)
    _validate_native_model = field_validator("default_model")(_validate_model)

    @field_validator("roles")
    @classmethod
    def _validate_role_names(cls, value: dict[str, CLIRoleConfig]) -> dict[str, CLIRoleConfig]:
        for name in value:
            _validate_name(name)
        return value


class ResolvedCLIRole(BaseModel):
    """Role snapshot, including prompt text read once at registry initialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    prompt_path: Path
    prompt_text: str = ""
    role_args: tuple[str, ...] = ()
    description: str | None = None
    model: str | None = None


class ResolvedNativeTask(BaseModel):
    """Native capability profile, separate from advisory configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    inherit_env: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    roles: dict[str, ResolvedCLIRole] = Field(default_factory=dict)
    default_model: str | None = None


class ResolvedCLIClient(BaseModel):
    """Frozen runtime fields; backend targets additionally snapshot nested mappings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    adapter: str
    executable: tuple[str, ...]
    working_dir: Path | None
    allowed_roots: tuple[Path, ...] = ()
    internal_args: tuple[str, ...] = ()
    config_args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int
    parser: str
    runner: str | None = None
    roles: dict[str, ResolvedCLIRole]
    default_model: str | None = None
    native_task: ResolvedNativeTask | None = None
    output_to_file: None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_adapter_default(cls, value: Any) -> Any:
        # Older callers construct built-in resolved clients directly in tests and integrations.
        if isinstance(value, dict) and "adapter" not in value:
            value = dict(value)
            value["adapter"] = str(value.get("name", "")).lower()
        return value

    def list_roles(self) -> list[str]:
        return list(self.roles)

    def get_role(self, role_name: str | None) -> ResolvedCLIRole:
        key = role_name or "default"
        if key not in self.roles:
            available = ", ".join(sorted(self.roles))
            raise KeyError(f"Role '{role_name}' not configured for CLI '{self.name}'. Available roles: {available}")
        return self.roles[key]
