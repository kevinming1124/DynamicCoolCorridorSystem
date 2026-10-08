"""Static graph entities with model-generated dynamic properties."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping


DynamicPropertyValue = float | str | None


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


def _updated_actuator_properties(
    current: Mapping[str, DynamicPropertyValue],
    name: str,
    value: DynamicPropertyValue,
    entity_id: str,
) -> dict[str, DynamicPropertyValue]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{entity_id}: dynamic property name cannot be blank")
    property_name = name.strip()
    if isinstance(value, bool) or not isinstance(value, (int, float, str, type(None))):
        raise ValueError(
            f"{entity_id}: actuator property '{property_name}' must be a "
            "number, string, or null"
        )
    if isinstance(value, (int, float)):
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError(
                f"{entity_id}: actuator property '{property_name}' must be finite"
            )
        value = numeric_value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(
                f"{entity_id}: actuator property '{property_name}' cannot be blank"
            )
    return {**current, property_name: value}


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
    """Fixed infrastructure with static limits and dynamic runtime properties."""

    actuator_id: str
    actuator_type: str
    anchor_node: str
    x_m: float
    y_m: float
    controlled_edges: tuple[str, ...] = ()
    recommended_edges: tuple[str, ...] = ()
    max_heat_reduction_c: float | None = None
    default_wait_time_s: float | None = None
    min_wait_time_s: float | None = None
    max_wait_time_s: float | None = None
    description: str = ""
    properties: Mapping[str, DynamicPropertyValue] = field(default_factory=dict)

    def with_property(
        self, name: str, value: DynamicPropertyValue
    ) -> "StaticActuator":
        property_name = name.strip() if isinstance(name, str) else name
        if property_name == "status":
            if self.actuator_type in {"misting_point", "directional_led"}:
                if not isinstance(value, str) or value not in {"on", "off"}:
                    raise ValueError(
                        f"{self.actuator_id}: 'status' must be 'on' or 'off'"
                    )
            elif value is not None:
                raise ValueError(
                    f"{self.actuator_id}: 'status' is not used by traffic signals"
                )
        elif property_name == "wait_time_s":
            if self.actuator_type == "traffic_signal":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"{self.actuator_id}: 'wait_time_s' must be a number"
                    )
                numeric_value = float(value)
                if not math.isfinite(numeric_value):
                    raise ValueError(
                        f"{self.actuator_id}: 'wait_time_s' must be finite"
                    )
                assert self.min_wait_time_s is not None
                assert self.max_wait_time_s is not None
                if not self.min_wait_time_s <= numeric_value <= self.max_wait_time_s:
                    raise ValueError(
                        f"{self.actuator_id}: 'wait_time_s' must be between "
                        f"{self.min_wait_time_s} and {self.max_wait_time_s}"
                    )
                value = numeric_value
            elif value is not None:
                raise ValueError(
                    f"{self.actuator_id}: 'wait_time_s' is only used by traffic signals"
                )
        elif property_name == "recommended_edge":
            if self.actuator_type == "directional_led":
                if value is not None and value not in self.recommended_edges:
                    raise ValueError(
                        f"{self.actuator_id}: 'recommended_edge' must be null or one "
                        "of the actuator's recommended edges"
                    )
            elif value is not None:
                raise ValueError(
                    f"{self.actuator_id}: 'recommended_edge' is only used by "
                    "directional LEDs"
                )

        return replace(
            self,
            properties=_updated_actuator_properties(
                self.properties, property_name, value, self.actuator_id
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "actuator_id": self.actuator_id,
            "actuator_type": self.actuator_type,
            "anchor_node": self.anchor_node,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "controlled_edges": list(self.controlled_edges),
            "recommended_edges": list(self.recommended_edges),
            "max_heat_reduction_c": self.max_heat_reduction_c,
            "default_wait_time_s": self.default_wait_time_s,
            "min_wait_time_s": self.min_wait_time_s,
            "max_wait_time_s": self.max_wait_time_s,
            "description": self.description,
            "properties": dict(self.properties),
        }
