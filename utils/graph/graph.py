"""Shared campus graph state passed between models."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .entities import DynamicPropertyValue, GraphEdge, GraphNode, StaticActuator


@dataclass(frozen=True)
class CampusGraph:
    nodes: Mapping[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    actuators: Mapping[str, StaticActuator] = field(default_factory=dict)

    def with_edge_property(
        self, name: str, values: Mapping[str, float]
    ) -> "CampusGraph":
        expected = {edge.edge_id for edge in self.edges}
        if set(values) != expected:
            raise ValueError("Edge property values must contain every edge ID exactly once")
        return replace(
            self,
            edges=tuple(edge.with_property(name, values[edge.edge_id]) for edge in self.edges),
        )

    def with_node_property(
        self, name: str, values: Mapping[str, float]
    ) -> "CampusGraph":
        if set(values) != set(self.nodes):
            raise ValueError("Node property values must contain every node ID exactly once")
        return replace(
            self,
            nodes={
                node_id: node.with_property(name, values[node_id])
                for node_id, node in self.nodes.items()
            },
        )

    def with_actuator_property(
        self,
        name: str,
        values: Mapping[str, DynamicPropertyValue],
    ) -> "CampusGraph":
        if set(values) != set(self.actuators):
            raise ValueError(
                "Actuator property values must contain every actuator ID exactly once"
            )
        return replace(
            self,
            actuators={
                actuator_id: actuator.with_property(name, values[actuator_id])
                for actuator_id, actuator in self.actuators.items()
            },
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.as_dict() for node in self.nodes.values()],
            "edges": [edge.as_dict() for edge in self.edges],
            "actuators": [actuator.as_dict() for actuator in self.actuators.values()],
        }
