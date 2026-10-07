"""Static graph entities with model-generated dynamic properties."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping


def _updated_properties(
    current: Mapping[str, float], name: str, value: float, entity_id: str
) -> dict[str, float]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{entity_id}: dynamic property name cannot be blank")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{entity_id}: dynamic property '{name}' must be a number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{entity_id}: dynamic property '{name}' must be finite")
    return {**current, name.strip(): numeric_value}


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    location_name: str
    location_type: str
    x_m: float
    y_m: float
    accessible: bool
    description: str
    properties: Mapping[str, float] = field(default_factory=dict)

    def with_property(self, name: str, value: float) -> "GraphNode":
        return replace(
            self,
            properties=_updated_properties(self.properties, name, value, self.node_id),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "location_name": self.location_name,
            "location_type": self.location_type,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "accessible": self.accessible,
            "description": self.description,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    from_node: str
    to_node: str
    distance_m: float
    walk_time_min: float
    surface_type: str
    accessible: bool
    properties: Mapping[str, float] = field(default_factory=dict)

    def with_property(self, name: str, value: float) -> "GraphEdge":
        return replace(
            self,
            properties=_updated_properties(self.properties, name, value, self.edge_id),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "distance_m": self.distance_m,
            "walk_time_min": self.walk_time_min,
            "surface_type": self.surface_type,
            "accessible": self.accessible,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class StaticActuator:
    """Fixed infrastructure that can change heat or pedestrian movement."""

    actuator_id: str
    actuator_type: str
    anchor_node: str
    x_m: float
    y_m: float
    controlled_edges: tuple[str, ...] = ()
    recommended_edges: tuple[str, ...] = ()
    cooling_radius_m: float | None = None
    max_heat_reduction_c: float | None = None
    default_wait_time_s: float | None = None
    min_wait_time_s: float | None = None
    max_wait_time_s: float | None = None
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "actuator_id": self.actuator_id,
            "actuator_type": self.actuator_type,
            "anchor_node": self.anchor_node,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "controlled_edges": list(self.controlled_edges),
            "recommended_edges": list(self.recommended_edges),
            "cooling_radius_m": self.cooling_radius_m,
            "max_heat_reduction_c": self.max_heat_reduction_c,
            "default_wait_time_s": self.default_wait_time_s,
            "min_wait_time_s": self.min_wait_time_s,
            "max_wait_time_s": self.max_wait_time_s,
            "description": self.description,
        }

