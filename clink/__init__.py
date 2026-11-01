"""Public helpers for clink components."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .registry import ClinkRegistry, get_registry

__all__ = ["ClinkRegistry", "get_registry"]


def __getattr__(name: str) -> Any:
    """Keep setup imports free of runtime dotenv and registry initialization."""
    if name in __all__:
        from .registry import ClinkRegistry, get_registry

        return {"ClinkRegistry": ClinkRegistry, "get_registry": get_registry}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
