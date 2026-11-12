"""Per-edge pedestrian heat-exposure model."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import HeatExposureModel, ModelConfig

__all__ = ["HeatExposureModel", "ModelConfig"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
