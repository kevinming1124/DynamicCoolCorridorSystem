"""Input and output envelopes shared by every graph model."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..graph import CampusGraph, DynamicPropertyValue


@dataclass(frozen=True)
class ModelInput:
    graph: CampusGraph
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelOutput:
    model_name: str
    property_target: str
    property_names: tuple[str, ...]
    property_values: Mapping[str, Mapping[str, DynamicPropertyValue]]
    graph: CampusGraph
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.property_target not in {"edge", "node", "actuator"}:
            raise ValueError("'property_target' must be 'edge', 'node', or 'actuator'")
        if not self.property_names:
            raise ValueError("'property_names' must contain at least one name")
        if any(
            not isinstance(name, str) or not name.strip()
            for name in self.property_names
        ):
            raise ValueError("Every property name must be a non-empty string")
        if len(set(self.property_names)) != len(self.property_names):
            raise ValueError("'property_names' must not contain duplicate names")
        if set(self.property_values) != set(self.property_names):
            raise ValueError("'property_values' must match 'property_names' exactly")

        if self.property_target == "edge":
            expected_ids = {edge.edge_id for edge in self.graph.edges}
        elif self.property_target == "node":
            expected_ids = set(self.graph.nodes)
        else:
            expected_ids = set(self.graph.actuators)

        for property_name in self.property_names:
            values = self.property_values[property_name]
            if set(values) != expected_ids:
                raise ValueError(
                    f"Property '{property_name}' must contain every "
                    f"{self.property_target} ID exactly once"
                )
            for entity_id, value in values.items():
                self._validate_value(entity_id, property_name, value)

    def _validate_value(
        self,
        entity_id: str,
        property_name: str,
        value: DynamicPropertyValue,
    ) -> None:
        if self.property_target != "actuator":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{entity_id}: property '{property_name}' must be a number"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    f"{entity_id}: property '{property_name}' must be finite"
                )
            return

        if isinstance(value, bool) or not isinstance(
            value, (int, float, str, type(None))
        ):
            raise ValueError(
                f"{entity_id}: actuator property '{property_name}' must be a "
                "number, string, or null"
            )
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError(
                f"{entity_id}: actuator property '{property_name}' must be finite"
            )
        if isinstance(value, str) and not value.strip():
            raise ValueError(
                f"{entity_id}: actuator property '{property_name}' cannot be blank"
            )

    def to_model_input(self, context: Mapping[str, Any] | None = None) -> ModelInput:
        return ModelInput(graph=self.graph, context=context or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "property_target": self.property_target,
            "property_names": list(self.property_names),
            "property_values": {
                name: dict(self.property_values[name]) for name in self.property_names
            },
            "graph": self.graph.as_dict(),
            "diagnostics": dict(self.diagnostics),
        }

    def save_json(self, path: str | Path) -> Path:
        """Save the complete model output as readable JSON for debugging."""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return output_path
