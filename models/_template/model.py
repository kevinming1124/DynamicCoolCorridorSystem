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
    GraphEdge,
    GraphNode,
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
    output_property: str
    input_property: str | None
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
            "output_property",
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
        if raw["property_target"] not in {"edge", "node"}:
            raise ValueError("'property_target' must be 'edge' or 'node'")
        if not isinstance(raw["output_property"], str) or not raw["output_property"].strip():
            raise ValueError("'output_property' must be a non-empty string")
        input_property = raw.get("input_property")
        if input_property is not None and (
            not isinstance(input_property, str) or not input_property.strip()
        ):
            raise ValueError("'input_property' must be null or a non-empty string")
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
            output_property=raw["output_property"].strip(),
            input_property=input_property.strip() if input_property else None,
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
            values = {
                edge.edge_id: self.calculate_edge(edge, model_input.graph)
                for edge in model_input.graph.edges
            }
            graph = model_input.graph.with_edge_property(
                self.config.output_property, values
            )
        else:
            values = {
                node_id: self.calculate_node(node, model_input.graph)
                for node_id, node in model_input.graph.nodes.items()
            }
            graph = model_input.graph.with_node_property(
                self.config.output_property, values
            )

        return ModelOutput(
            model_name=self.config.model_name,
            property_target=self.config.property_target,
            property_name=self.config.output_property,
            property_values=values,
            graph=graph,
            diagnostics={
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "input_property": self.config.input_property,
            },
        )

    def calculate_edge(self, edge: GraphEdge, graph: CampusGraph) -> float:
        """Calculate one dynamic edge property.

        HUMAN-EDITABLE SECTION
        ----------------------
        When input_property is null, the example starts with distance. When it
        names an earlier property, the example uses that previous model result.
        """
        del graph  # Available for formulas that need neighboring nodes or edges.
        base_value = self._input_value(edge.properties, edge.distance_m, edge.edge_id)
        value = base_value * self._parameter("base_weight")
        if not edge.accessible:
            value += self._parameter("inaccessible_penalty")
        return value

    def calculate_node(self, node: GraphNode, graph: CampusGraph) -> float:
        """Calculate one dynamic node property.

        HUMAN-EDITABLE SECTION
        ----------------------
        The default uses an earlier node property when configured; otherwise it
        uses the node's number of incident edges.
        """
        incident_edge_count = sum(
            edge.from_node == node.node_id or edge.to_node == node.node_id
            for edge in graph.edges
        )
        base_value = self._input_value(
            node.properties, float(incident_edge_count), node.node_id
        )
        return base_value * self._parameter("base_weight")

    def _input_value(
        self,
        properties: Mapping[str, float],
        default: float,
        entity_id: str,
    ) -> float:
        if self.config.input_property is None:
            return default
        try:
            return properties[self.config.input_property]
        except KeyError as exc:
            raise ValueError(
                f"{entity_id}: input property '{self.config.input_property}' is missing"
            ) from exc

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
