"""Environment model: per-edge campus conditions for one time of day."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import EnvironmentModel, ModelConfig

__all__ = [
    "EnvironmentModel",
    "ModelConfig",
]


def __getattr__(name: str) -> Any:
    """Expose model classes without eagerly loading the executable module."""
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
