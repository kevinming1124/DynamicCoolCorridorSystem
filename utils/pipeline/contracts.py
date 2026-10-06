"""Input and output envelopes shared by every graph model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..graph import CampusGraph


@dataclass(frozen=True)
class ModelInput:
    graph: CampusGraph
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelOutput:
    model_name: str
    property_target: str
    property_name: str
    property_values: Mapping[str, float]
    graph: CampusGraph
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_model_input(self, context: Mapping[str, Any] | None = None) -> ModelInput:
        return ModelInput(graph=self.graph, context=context or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "property_target": self.property_target,
            "property_name": self.property_name,
            "property_values": dict(self.property_values),
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

