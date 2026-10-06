"""Reusable starting point for Dynamic Cool Corridor System models."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import ModelConfig, ModelTemplate

__all__ = [
    "ModelConfig",
    "ModelTemplate",
]


def __getattr__(name: str) -> Any:
    """Expose template classes without eagerly loading the executable module."""
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

