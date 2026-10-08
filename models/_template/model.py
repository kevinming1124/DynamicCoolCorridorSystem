"""Human-editable graph transformation model template.

Shared graph types, dataset loading, and pipeline contracts live in the
``utils`` package. This module contains only model-specific configuration
and calculation logic.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from utils import (
    CampusGraph,
    DynamicPropertyValue,
    GraphEdge,
    GraphNode,
    StaticActuator,
    ModelInput,
    ModelOutput,
    load_campus_graph,
)


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "tiny_campus_block"


@dataclass(frozen=True)
class ModelConfig:
    """Settings specific to this model."""

    model_name: str
    description: str
    enabled: bool
    property_target: str
    output_properties: tuple[str, ...]
    input_properties: tuple[str, ...]
    parameters: Mapping[str, float]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "ModelConfig":
        config_path = Path(path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {config_path} at line {exc.lineno}, column {exc.colno}"
            ) from exc

        required = {
            "model_name",
            "description",
            "enabled",
            "property_target",
            "output_properties",
            "input_properties",
            "parameters",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Missing configuration fields: {', '.join(sorted(missing))}")
        if not isinstance(raw["model_name"], str) or not raw["model_name"].strip():
            raise ValueError("'model_name' must be a non-empty string")
        if not isinstance(raw["description"], str):
            raise ValueError("'description' must be a string")
        if not isinstance(raw["enabled"], bool):
            raise ValueError("'enabled' must be true or false")
        if raw["property_target"] not in {"edge", "node", "actuator"}:
            raise ValueError(
                "'property_target' must be 'edge', 'node', or 'actuator'"
            )
        raw_output_properties = raw["output_properties"]
        if not isinstance(raw_output_properties, list) or not raw_output_properties:
            raise ValueError("'output_properties' must be a non-empty JSON array")
        if any(
            not isinstance(name, str) or not name.strip()
            for name in raw_output_properties
        ):
            raise ValueError(
                "Every item in 'output_properties' must be a non-empty string"
            )
        output_properties = tuple(name.strip() for name in raw_output_properties)
        if len(set(output_properties)) != len(output_properties):
            raise ValueError("'output_properties' must not contain duplicate names")
        raw_input_properties = raw["input_properties"]
        if not isinstance(raw_input_properties, list):
            raise ValueError("'input_properties' must be a JSON array")
        if any(
            not isinstance(name, str) or not name.strip()
            for name in raw_input_properties
        ):
            raise ValueError(
                "Every item in 'input_properties' must be a non-empty string"
            )
        input_properties = tuple(name.strip() for name in raw_input_properties)
        if len(set(input_properties)) != len(input_properties):
            raise ValueError("'input_properties' must not contain duplicate names")
        if not isinstance(raw["parameters"], dict):
            raise ValueError("'parameters' must be a JSON object")
        if not isinstance(raw.get("metadata", {}), dict):
            raise ValueError("'metadata' must be a JSON object")

        parameters: dict[str, float] = {}
        for name, value in raw["parameters"].items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Parameter '{name}' must be a number")
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError(f"Parameter '{name}' must be finite")
            parameters[name] = numeric_value

        return cls(
            model_name=raw["model_name"].strip(),
            description=raw["description"],
            enabled=raw["enabled"],
            property_target=raw["property_target"],
            output_properties=output_properties,
            input_properties=input_properties,
            parameters=parameters,
            metadata=raw.get("metadata", {}),
        )


class ModelTemplate:
    """One reusable graph-property transformation stage."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    @classmethod
    def from_config(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "ModelTemplate":
        return cls(ModelConfig.from_file(path))

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        if self.config.property_target == "edge":
            entity_values = {
                edge.edge_id: self._validated_output_values(
                    self.calculate_edge(edge, model_input.graph), edge.edge_id
                )
                for edge in model_input.graph.edges
            }
        elif self.config.property_target == "node":
            entity_values = {
                node_id: self._validated_output_values(
                    self.calculate_node(node, model_input.graph), node_id
                )
                for node_id, node in model_input.graph.nodes.items()
            }
        else:
            entity_values = {
                actuator_id: self._validated_output_values(
                    self.calculate_actuator(actuator, model_input.graph), actuator_id
                )
                for actuator_id, actuator in model_input.graph.actuators.items()
            }

        values_by_property = {
            property_name: {
                entity_id: values[property_name]
                for entity_id, values in entity_values.items()
            }
            for property_name in self.config.output_properties
        }

        graph = model_input.graph
        for property_name, values in values_by_property.items():
            if self.config.property_target == "edge":
                graph = graph.with_edge_property(property_name, values)
            elif self.config.property_target == "node":
                graph = graph.with_node_property(property_name, values)
            else:
                graph = graph.with_actuator_property(property_name, values)

        return ModelOutput(
            model_name=self.config.model_name,
            property_target=self.config.property_target,
            property_names=self.config.output_properties,
            property_values=values_by_property,
            graph=graph,
            diagnostics={
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "actuator_count": len(graph.actuators),
                "input_properties": list(self.config.input_properties),
            },
        )

    def calculate_edge(
        self, edge: GraphEdge, graph: CampusGraph
    ) -> Mapping[str, float]:
        """Calculate all configured dynamic edge properties.

        HUMAN-EDITABLE SECTION
        ----------------------
        With no input properties, the example starts with distance. When one or
        more are configured, the example sums those earlier model results.
        """
        del graph  # Available for formulas that need neighboring nodes or edges.
        input_values = self._numeric_input_values(edge.properties, edge.edge_id)
        base_value = sum(input_values.values()) if input_values else edge.distance_m
        return {
            "distance_cost": base_value * self._parameter("base_weight"),
            "accessibility_cost": (
                0.0 if edge.accessible else self._parameter("inaccessible_penalty")
            ),
        }

    def calculate_node(
        self, node: GraphNode, graph: CampusGraph
    ) -> Mapping[str, float]:
        """Calculate all configured dynamic node properties.

        HUMAN-EDITABLE SECTION
        ----------------------
        The default sums earlier node properties when configured; otherwise it
        uses the node's number of incident edges.
        """
        incident_edge_count = sum(
            edge.from_node == node.node_id or edge.to_node == node.node_id
            for edge in graph.edges
        )
        input_values = self._numeric_input_values(node.properties, node.node_id)
        base_value = (
            sum(input_values.values())
            if input_values
            else float(incident_edge_count)
        )
        return {
            "distance_cost": base_value * self._parameter("base_weight"),
            "accessibility_cost": (
                0.0 if node.accessible else self._parameter("inaccessible_penalty")
            ),
        }

    def calculate_actuator(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> Mapping[str, DynamicPropertyValue]:
        """Calculate all configured dynamic actuator properties.

        HUMAN-EDITABLE SECTION
        ----------------------
        The starter preserves the named actuator properties. A control model can
        use ``input_values`` and graph state to return new status, wait-time, or
        recommended-edge values.
        """
        del graph  # Available for formulas that need nodes, edges, or actuators.
        input_values = self._input_values(actuator.properties, actuator.actuator_id)
        return {
            name: input_values.get(name, actuator.properties.get(name))
            for name in self.config.output_properties
        }

    def _validated_output_values(
        self,
        values: Mapping[str, DynamicPropertyValue],
        entity_id: str,
    ) -> dict[str, DynamicPropertyValue]:
        if not isinstance(values, Mapping):
            raise ValueError(f"{entity_id}: calculation must return a mapping")

        expected = set(self.config.output_properties)
        actual = set(values)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            details = []
            if missing:
                details.append(f"missing {missing}")
            if unexpected:
                details.append(f"unexpected {unexpected}")
            raise ValueError(
                f"{entity_id}: calculated properties do not match "
                f"'output_properties' ({'; '.join(details)})"
            )

        validated: dict[str, DynamicPropertyValue] = {}
        for name in self.config.output_properties:
            value = values[name]
            if self.config.property_target != "actuator":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"{entity_id}: output property '{name}' must be a number"
                    )
                numeric_value = float(value)
                if not math.isfinite(numeric_value):
                    raise ValueError(
                        f"{entity_id}: output property '{name}' must be finite"
                    )
                validated[name] = numeric_value
                continue

            if isinstance(value, bool) or not isinstance(
                value, (int, float, str, type(None))
            ):
                raise ValueError(
                    f"{entity_id}: actuator property '{name}' must be a "
                    "number, string, or null"
                )
            if isinstance(value, (int, float)):
                numeric_value = float(value)
                if not math.isfinite(numeric_value):
                    raise ValueError(
                        f"{entity_id}: actuator property '{name}' must be finite"
                    )
                value = numeric_value
            elif isinstance(value, str):
                value = value.strip()
                if not value:
                    raise ValueError(
                        f"{entity_id}: actuator property '{name}' cannot be blank"
                    )
            validated[name] = value
        return validated

    def _input_values(
        self,
        properties: Mapping[str, DynamicPropertyValue],
        entity_id: str,
    ) -> dict[str, DynamicPropertyValue]:
        missing = [
            name for name in self.config.input_properties if name not in properties
        ]
        if missing:
            raise ValueError(
                f"{entity_id}: input properties are missing: {', '.join(missing)}"
            )
        return {name: properties[name] for name in self.config.input_properties}

    def _numeric_input_values(
        self,
        properties: Mapping[str, float],
        entity_id: str,
    ) -> dict[str, float]:
        values = self._input_values(properties, entity_id)
        numeric_values: dict[str, float] = {}
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{entity_id}: input property '{name}' must be numeric"
                )
            numeric_values[name] = float(value)
        return numeric_values

    def _parameter(self, name: str) -> float:
        try:
            return self.config.parameters[name]
        except KeyError as exc:
            raise ValueError(f"Required parameter '{name}' is missing") from exc


if __name__ == "__main__":
    model = ModelTemplate.from_config()
    graph = load_campus_graph(DEFAULT_DATASET_PATH)
    output = model.predict(ModelInput(graph=graph))
    print(json.dumps(output.as_dict(), indent=2))
