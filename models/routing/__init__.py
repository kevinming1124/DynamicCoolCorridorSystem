"""Routing model: comfort-vs-speed walking routes and pedestrian simulation."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import Planner, RoutingModel, RoutingSettings

__all__ = ["Planner", "RoutingModel", "RoutingSettings"]


# Import lazily (same pattern as the other models), so running a file in this
# package directly with `python -m` doesn't load it twice.
def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
