"""Final requirements verification stage."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import VerificationModel

__all__ = ["VerificationModel"]


def __getattr__(name: str) -> Any:
    """Expose the model without eagerly loading its executable module."""
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
