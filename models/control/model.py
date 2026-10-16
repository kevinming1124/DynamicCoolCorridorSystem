"""Control the existing state of campus actuators."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from utils import (
    CampusGraph,
    StaticActuator,
    ModelInput,
    ModelOutput,
    load_campus_graph,
)


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "tiny_campus_block"
DEFAULT_MOCK_OUTPUTS_PATH = Path(__file__).with_name("mock_model_outputs.json")

AIR_TEMPERATURE_PROPERTY = "air_temperature_f"
HEAT_EXPOSURE_PROPERTY = "heat_exposure"
PREDICTED_HEAT_EXPOSURE_PROPERTY = "predicted_heat_exposure"
PREDICTED_CROWD_DENSITY_PROPERTY = "predicted_crowd_density"
MISTING_TEMPERATURE_THRESHOLD_F = 95.0
HIGH_HEAT_EXPOSURE_THRESHOLD = 0.7
HIGH_CROWD_DENSITY_THRESHOLD = 0.7


def load_mock_model_outputs(
    graph: CampusGraph,
    path: str | Path = DEFAULT_MOCK_OUTPUTS_PATH,
) -> CampusGraph:
    """Add fake environment, Heat Exposure, and Heat Prediction outputs."""
    mock_path = Path(path)
    try:
        raw = json.loads(mock_path.read_text(encoding="utf-8"))
        air_temperatures = raw["campus_environment"]["property_values"][
            AIR_TEMPERATURE_PROPERTY
        ]
        heat_exposures = raw["heat_exposure"]["property_values"][
            HEAT_EXPOSURE_PROPERTY
        ]
        predicted_heat_exposures = raw["heat_prediction"]["property_values"][
            PREDICTED_HEAT_EXPOSURE_PROPERTY
        ]
        predicted_crowd_densities = raw["heat_prediction"]["property_values"][
            PREDICTED_CROWD_DENSITY_PROPERTY
        ]
    except FileNotFoundError as exc:
        raise ValueError(f"Mock heat output file not found: {mock_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {mock_path} at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid mock heat output structure in {mock_path}") from exc

    graph = graph.with_node_property(AIR_TEMPERATURE_PROPERTY, air_temperatures)
    graph = graph.with_edge_property(HEAT_EXPOSURE_PROPERTY, heat_exposures)
    graph = graph.with_edge_property(
        PREDICTED_HEAT_EXPOSURE_PROPERTY, predicted_heat_exposures
    )
    return graph.with_edge_property(
        PREDICTED_CROWD_DENSITY_PROPERTY, predicted_crowd_densities
    )


@dataclass(frozen=True)
class ModelConfig:
    """Basic settings for the actuator control model."""

    model_name: str
    description: str
    enabled: bool

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

        required = {"model_name", "description", "enabled"}
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Missing configuration fields: {', '.join(sorted(missing))}")
        if not isinstance(raw["model_name"], str) or not raw["model_name"].strip():
            raise ValueError("'model_name' must be a non-empty string")
        if not isinstance(raw["description"], str):
            raise ValueError("'description' must be a string")
        if not isinstance(raw["enabled"], bool):
            raise ValueError("'enabled' must be true or false")

        return cls(
            model_name=raw["model_name"].strip(),
            description=raw["description"],
            enabled=raw["enabled"],
        )


class ControlModel:
    """Dispatch and apply controls for each campus actuator."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    @classmethod
    def from_config(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "ControlModel":
        return cls(ModelConfig.from_file(path))

    def control(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        actuators = {
            actuator_id: self.control_actuator(actuator, model_input.graph)
            for actuator_id, actuator in model_input.graph.actuators.items()
        }
        graph = replace(model_input.graph, actuators=actuators)

        return ModelOutput(
            model_name=self.config.model_name,
            property_target=None,
            property_names=(),
            property_values={},
            graph=graph,
        )

    def predict(self, model_input: ModelInput) -> ModelOutput:
        """Run this controller through the shared pipeline interface."""
        return self.control(model_input)

    def control_actuator(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Call the controller that matches ``actuator.actuator_type``."""
        if actuator.actuator_type == "misting_point":
            return self.control_misting_point(actuator, graph)
        if actuator.actuator_type == "traffic_signal":
            return self.control_traffic_signal(actuator, graph)
        if actuator.actuator_type == "directional_led":
            return self.control_directional_led(actuator, graph)

        raise ValueError(
            f"{actuator.actuator_id}: unsupported actuator type "
            f"'{actuator.actuator_type}'"
        )

    def control_misting_point(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Control misting from local temperature and current route exposure."""
        anchor_node = graph.nodes[actuator.anchor_node]
        air_temperature = self._numeric_property(
            anchor_node.properties,
            AIR_TEMPERATURE_PROPERTY,
            anchor_node.node_id,
        )
        incident_edges = (
            edge
            for edge in graph.edges
            if actuator.anchor_node in {edge.from_node, edge.to_node}
        )
        heat_exposure = max(
            self._numeric_property(
                edge.properties, HEAT_EXPOSURE_PROPERTY, edge.edge_id
            )
            for edge in incident_edges
        )
        status = (
            "on"
            if air_temperature >= MISTING_TEMPERATURE_THRESHOLD_F
            or heat_exposure >= HIGH_HEAT_EXPOSURE_THRESHOLD
            else "off"
        )
        print(
            f"Controlling misting point {actuator.actuator_id}: "
            f"{air_temperature:.1f} F, heat exposure {heat_exposure:.2f} "
            f"-> {status}"
        )
        return actuator.with_property("status", status)

    def control_traffic_signal(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Reduce wait time for current/predicted heat or crowd risk."""
        heat_exposure = max(
            self._edge_property(graph, edge_id, HEAT_EXPOSURE_PROPERTY)
            for edge_id in actuator.controlled_edges
        )
        predicted_heat_exposure = max(
            self._edge_property(
                graph, edge_id, PREDICTED_HEAT_EXPOSURE_PROPERTY
            )
            for edge_id in actuator.controlled_edges
        )
        predicted_crowd_density = max(
            self._edge_property(
                graph, edge_id, PREDICTED_CROWD_DENSITY_PROPERTY
            )
            for edge_id in actuator.controlled_edges
        )
        if actuator.min_wait_time_s is None or actuator.default_wait_time_s is None:
            raise ValueError(
                f"{actuator.actuator_id}: traffic signal wait-time limits are missing"
            )
        wait_time_s = (
            actuator.min_wait_time_s
            if heat_exposure >= HIGH_HEAT_EXPOSURE_THRESHOLD
            or predicted_heat_exposure >= HIGH_HEAT_EXPOSURE_THRESHOLD
            or predicted_crowd_density >= HIGH_CROWD_DENSITY_THRESHOLD
            else actuator.default_wait_time_s
        )
        print(
            f"Controlling traffic signal {actuator.actuator_id}: "
            f"current heat {heat_exposure:.2f}, predicted heat "
            f"{predicted_heat_exposure:.2f}, predicted crowd "
            f"{predicted_crowd_density:.2f} -> wait {wait_time_s:.0f} s"
        )
        return actuator.with_property("status", "on").with_property(
            "wait_time_s", wait_time_s
        )

    def control_directional_led(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Recommend the linked edge with the lowest predicted heat risk."""
        recommended_edge = min(
            actuator.recommended_edges,
            key=lambda edge_id: (
                self._edge_property(
                    graph, edge_id, PREDICTED_HEAT_EXPOSURE_PROPERTY
                ),
                self._edge_property(
                    graph, edge_id, PREDICTED_CROWD_DENSITY_PROPERTY
                ),
            ),
        )
        predicted_heat_exposure = self._edge_property(
            graph, recommended_edge, PREDICTED_HEAT_EXPOSURE_PROPERTY
        )
        predicted_crowd_density = self._edge_property(
            graph, recommended_edge, PREDICTED_CROWD_DENSITY_PROPERTY
        )
        print(
            f"Controlling directional LED {actuator.actuator_id}: "
            f"recommend {recommended_edge} (predicted heat "
            f"{predicted_heat_exposure:.2f}, predicted crowd "
            f"{predicted_crowd_density:.2f})"
        )
        return actuator.with_property("status", "on").with_property(
            "recommended_edge", recommended_edge
        )

    def _edge_property(
        self, graph: CampusGraph, edge_id: str, property_name: str
    ) -> float:
        edge = next(
            (candidate for candidate in graph.edges if candidate.edge_id == edge_id),
            None,
        )
        if edge is None:
            raise ValueError(f"Unknown edge: {edge_id}")
        return self._numeric_property(edge.properties, property_name, edge.edge_id)

    @staticmethod
    def _numeric_property(
        properties: Mapping[str, object], property_name: str, entity_id: str
    ) -> float:
        try:
            value = properties[property_name]
        except KeyError as exc:
            raise ValueError(
                f"{entity_id}: required dynamic property '{property_name}' is missing"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{entity_id}: dynamic property '{property_name}' must be numeric"
            )
        return float(value)


if __name__ == "__main__":
    model = ControlModel.from_config()
    graph = load_campus_graph(DEFAULT_DATASET_PATH)
    graph = load_mock_model_outputs(graph)
    output = model.control(ModelInput(graph=graph))
    for actuator in output.graph.actuators.values():
        print(f"{actuator.actuator_id}: {dict(actuator.properties)}")
