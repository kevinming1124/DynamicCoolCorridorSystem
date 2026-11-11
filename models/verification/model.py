"""Final hybrid requirements verification model."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph

from .checks import (
    EXPECTED_ENTRY_TIME,
    FALLBACK_SELECTED,
    PREDICTED_EXPOSURE,
    PREDICTED_TRAVEL_TIME,
    REQUIRED_RELAXATION,
    ROUTE_ORDER,
    check_actuator_response,
    check_arrival,
    check_fallback,
    edge_value,
    route_metrics,
    selected_route,
)
from .strel_adapter import RouteSafetyMonitor


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_MOCK_PATH = Path(__file__).with_name("mock_rounds.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "tiny_campus_block"
ROUTE_ORIGIN = "route_origin"
ROUTE_DESTINATION = "route_destination"


@dataclass(frozen=True)
class VerificationConfig:
    model_name: str
    description: str
    enabled: bool
    property_target: str
    output_properties: tuple[str, ...]
    input_properties: tuple[str, ...]
    parameters: Mapping[str, float]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(
        cls, path: str | Path = DEFAULT_CONFIG_PATH
    ) -> "VerificationConfig":
        config_path = Path(path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {config_path} at line {exc.lineno}, "
                f"column {exc.colno}"
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
            raise ValueError(f"Missing configuration fields: {sorted(missing)}")
        if not isinstance(raw["model_name"], str) or not raw["model_name"].strip():
            raise ValueError("'model_name' must be a non-empty string")
        if not isinstance(raw["description"], str):
            raise ValueError("'description' must be a string")
        if not isinstance(raw["enabled"], bool):
            raise ValueError("'enabled' must be true or false")
        if raw["property_target"] != "edge":
            raise ValueError("The verification model must target edges")

        output_properties = _string_tuple(raw, "output_properties")
        input_properties = _string_tuple(raw, "input_properties")
        expected_outputs = {
            "verification_selected_edge_safe",
            "verification_route_safe_normal",
            "verification_route_safe_applied",
            "verification_route_usable",
            "verification_bounded_arrival_valid",
            "verification_fallback_valid",
            "verification_actuator_response_valid",
            "verification_overall_valid",
        }
        if set(output_properties) != expected_outputs:
            raise ValueError(
                f"'output_properties' must contain {sorted(expected_outputs)}"
            )
        expected_inputs = {
            "route_order",
            "expected_entry_time_s",
            "predicted_exposure_at_entry",
            "predicted_travel_time_s",
            "fallback_selected",
            "required_threshold_relaxation",
            "baseline_temperature_f",
            "misting_temperature_effect_f",
            "controlled_temperature_f",
            "baseline_predicted_exposure_at_entry",
            "misting_exposure_effect_at_entry",
            "baseline_travel_time_s",
            "signal_travel_time_effect_s",
            "pedestrian_demand",
        }
        if set(input_properties) != expected_inputs:
            raise ValueError(f"'input_properties' must contain {sorted(expected_inputs)}")

        parameters_raw = raw["parameters"]
        if not isinstance(parameters_raw, dict):
            raise ValueError("'parameters' must be an object")
        parameters: dict[str, float] = {}
        for name, value in parameters_raw.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Parameter '{name}' must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"Parameter '{name}' must be finite")
            parameters[name] = numeric
        required_parameters = {
            "unsafe_exposure_threshold",
            "maximum_arrival_ratio",
            "misting_influence_radius_m",
            "maximum_exposure_reduction_per_f",
            "minimum_controlled_temperature_f",
            "comparison_tolerance",
        }
        missing_parameters = required_parameters - parameters.keys()
        if missing_parameters:
            raise ValueError(
                f"Missing verification parameters: {sorted(missing_parameters)}"
            )
        if parameters["maximum_arrival_ratio"] <= 0.0:
            raise ValueError("maximum_arrival_ratio must be positive")
        if parameters["misting_influence_radius_m"] < 0.0:
            raise ValueError("misting_influence_radius_m cannot be negative")
        if parameters["maximum_exposure_reduction_per_f"] < 0.0:
            raise ValueError("maximum_exposure_reduction_per_f cannot be negative")
        if parameters["comparison_tolerance"] < 0.0:
            raise ValueError("comparison_tolerance cannot be negative")

        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("'metadata' must be an object")
        return cls(
            model_name=raw["model_name"].strip(),
            description=raw["description"],
            enabled=raw["enabled"],
            property_target=raw["property_target"],
            output_properties=output_properties,
            input_properties=input_properties,
            parameters=parameters,
            metadata=metadata,
        )


def _string_tuple(raw: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"'{name}' must be an array of non-empty strings")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"'{name}' cannot contain duplicates")
    return result


class VerificationModel:
    """Verify the final route and same-round attributed control response."""

    def __init__(
        self,
        config: VerificationConfig,
        route_monitor: RouteSafetyMonitor,
    ) -> None:
        self.config = config
        self.route_monitor = route_monitor

    @classmethod
    def from_config(
        cls, path: str | Path = DEFAULT_CONFIG_PATH
    ) -> "VerificationModel":
        config_path = Path(path)
        config = VerificationConfig.from_file(config_path)
        formula_file = config.metadata.get("formula_file")
        formula_name = config.metadata.get("formula_name")
        if not isinstance(formula_file, str) or not formula_file.strip():
            raise ValueError("metadata.formula_file must be a non-empty string")
        if not isinstance(formula_name, str) or not formula_name.strip():
            raise ValueError("metadata.formula_name must be a non-empty string")
        return cls(
            config,
            RouteSafetyMonitor(
                config_path.parent / formula_file.strip(), formula_name.strip()
            ),
        )

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        graph = model_input.graph
        self._validate_inputs(graph)
        origin = self._marked_node(graph, ROUTE_ORIGIN)
        destination = self._marked_node(graph, ROUTE_DESTINATION)
        route = selected_route(graph, origin, destination)

        threshold = self._parameter("unsafe_exposure_threshold")
        maximum_ratio = self._parameter("maximum_arrival_ratio")
        tolerance = self._parameter("comparison_tolerance")
        self._validate_entry_times(route, tolerance)
        metrics = route_metrics(route)
        arrival = check_arrival(
            graph,
            route,
            origin,
            destination,
            maximum_ratio,
            tolerance,
        )
        fallback_selected = self._common_binary(graph, FALLBACK_SELECTED)
        reported_relaxation = self._common_value(graph, REQUIRED_RELAXATION)
        if reported_relaxation < 0.0:
            raise ValueError("required_threshold_relaxation cannot be negative")
        fallback = check_fallback(
            graph,
            metrics,
            origin,
            destination,
            threshold,
            arrival.maximum_allowed_time_s,
            fallback_selected,
            reported_relaxation,
            tolerance,
        )

        route_safe_normal = self.route_monitor.evaluate(route, threshold + tolerance)
        applied_threshold = (
            threshold + reported_relaxation if fallback_selected else threshold
        )
        route_safe_applied = self.route_monitor.evaluate(
            route, applied_threshold + tolerance
        )
        actuator = check_actuator_response(
            graph,
            misting_radius_m=self._parameter("misting_influence_radius_m"),
            maximum_exposure_reduction_per_f=self._parameter(
                "maximum_exposure_reduction_per_f"
            ),
            minimum_controlled_temperature_f=self._parameter(
                "minimum_controlled_temperature_f"
            ),
            supported_saturation_reasons=self._supported_saturation_reasons(),
            tolerance=tolerance,
        )

        safe_case = bool(
            fallback.safe_bounded_route_exists
            and not fallback_selected
            and route_safe_normal
            and arrival.valid
            and fallback.valid
            and actuator.valid
        )
        fallback_case = bool(
            not fallback.safe_bounded_route_exists
            and fallback_selected
            and not route_safe_normal
            and route_safe_applied
            and arrival.valid
            and fallback.valid
            and actuator.valid
        )
        overall_valid = safe_case or fallback_case
        route_usable = overall_valid
        outcome = (
            "SAFE"
            if safe_case
            else "INFEASIBLE_FALLBACK"
            if fallback_case
            else "INVALID"
        )

        selected_ids = set(metrics.edge_ids)
        selected_edge_safe = {
            edge.edge_id: (
                1.0
                if edge_value(edge, PREDICTED_EXPOSURE)
                <= applied_threshold + tolerance
                else 0.0
            )
            if edge.edge_id in selected_ids
            else -1.0
            for edge in graph.edges
        }
        repeated = {
            "verification_route_safe_normal": route_safe_normal,
            "verification_route_safe_applied": route_safe_applied,
            "verification_route_usable": route_usable,
            "verification_bounded_arrival_valid": arrival.valid,
            "verification_fallback_valid": fallback.valid,
            "verification_actuator_response_valid": actuator.valid,
            "verification_overall_valid": overall_valid,
        }
        values_by_property: dict[str, dict[str, float]] = {
            "verification_selected_edge_safe": selected_edge_safe
        }
        for name, value in repeated.items():
            values_by_property[name] = {
                edge.edge_id: 1.0 if value else 0.0 for edge in graph.edges
            }

        updated_graph = graph
        for name in self.config.output_properties:
            updated_graph = updated_graph.with_edge_property(
                name, values_by_property[name]
            )

        diagnostics = {
            "outcome": outcome,
            "route": {
                "origin": origin,
                "destination": destination,
                "edge_ids": list(metrics.edge_ids),
                "maximum_exposure": metrics.maximum_exposure,
                "cumulative_exposure": metrics.cumulative_exposure,
                "travel_time_s": metrics.travel_time_s,
                "safe_at_normal_threshold": route_safe_normal,
                "safe_at_applied_threshold": route_safe_applied,
                "normal_threshold": threshold,
                "applied_threshold": applied_threshold,
            },
            "bounded_arrival": {
                "valid": arrival.valid,
                "selected_time_s": arrival.selected_time_s,
                "shortest_time_s": arrival.shortest_time_s,
                "maximum_allowed_time_s": arrival.maximum_allowed_time_s,
                "ratio": arrival.ratio,
                "reference_ignores_safety": True,
            },
            "fallback": {
                "valid": fallback.valid,
                "selected": fallback_selected,
                "safe_bounded_route_exists": fallback.safe_bounded_route_exists,
                "reported_relaxation": reported_relaxation,
                "expected_relaxation": fallback.expected_relaxation,
                "optimal_edge_ids": list(fallback.optimal_path.edge_ids),
                "reason": fallback.reason,
            },
            "actuator_response": {
                "valid": actuator.valid,
                "violations": list(actuator.violations),
                "changed_temperature_edges": list(
                    actuator.changed_temperature_edges
                ),
                "changed_exposure_edges": list(actuator.changed_exposure_edges),
                "changed_travel_time_edges": list(
                    actuator.changed_travel_time_edges
                ),
            },
            "engines": {
                "route_safety": "moonlight_strel",
                "bounded_arrival": "python",
                "fallback": "python",
                "actuator_response": "python",
            },
        }
        return ModelOutput(
            model_name=self.config.model_name,
            property_target=self.config.property_target,
            property_names=self.config.output_properties,
            property_values=values_by_property,
            graph=updated_graph,
            diagnostics=diagnostics,
        )

    def _validate_inputs(self, graph: CampusGraph) -> None:
        for edge in graph.edges:
            missing = [
                name for name in self.config.input_properties if name not in edge.properties
            ]
            if missing:
                raise ValueError(
                    f"{edge.edge_id}: verification inputs are missing: "
                    f"{', '.join(missing)}"
                )
            for name in self.config.input_properties:
                edge_value(edge, name)

        required_nodes = self.config.metadata.get("required_node_properties")
        if not isinstance(required_nodes, list) or any(
            not isinstance(name, str) or not name.strip() for name in required_nodes
        ):
            raise ValueError("metadata.required_node_properties must be a string array")
        for node_id, node in graph.nodes.items():
            missing = [name for name in required_nodes if name not in node.properties]
            if missing:
                raise ValueError(
                    f"{node_id}: verification node inputs are missing: "
                    f"{', '.join(missing)}"
                )

    @staticmethod
    def _marked_node(graph: CampusGraph, name: str) -> str:
        marked = []
        for node_id, node in graph.nodes.items():
            value = node.properties.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{node_id}: '{name}' must be numeric")
            if math.isclose(float(value), 1.0):
                marked.append(node_id)
        if len(marked) != 1:
            raise ValueError(f"Exactly one node must have '{name}' equal to 1")
        return marked[0]

    @staticmethod
    def _validate_entry_times(route: tuple[Any, ...], tolerance: float) -> None:
        for previous, current in zip(route, route[1:]):
            expected = edge_value(previous, EXPECTED_ENTRY_TIME) + edge_value(
                previous, PREDICTED_TRAVEL_TIME
            )
            actual = edge_value(current, EXPECTED_ENTRY_TIME)
            if not math.isclose(expected, actual, abs_tol=tolerance):
                raise ValueError(
                    f"{current.edge_id}: expected entry time is inconsistent "
                    "with the preceding controlled travel time"
                )

    def _common_value(self, graph: CampusGraph, name: str) -> float:
        values = [edge_value(edge, name) for edge in graph.edges]
        first = values[0]
        tolerance = self._parameter("comparison_tolerance")
        if any(not math.isclose(value, first, abs_tol=tolerance) for value in values[1:]):
            raise ValueError(f"'{name}' must agree across every edge")
        return first

    def _common_binary(self, graph: CampusGraph, name: str) -> bool:
        value = self._common_value(graph, name)
        if value not in {0.0, 1.0}:
            raise ValueError(f"'{name}' must be 0 or 1")
        return value == 1.0

    def _parameter(self, name: str) -> float:
        try:
            return self.config.parameters[name]
        except KeyError as exc:
            raise ValueError(f"Required parameter '{name}' is missing") from exc

    def _supported_saturation_reasons(self) -> frozenset[str]:
        reasons = self.config.metadata.get("supported_saturation_reasons")
        if not isinstance(reasons, list) or any(
            not isinstance(reason, str) or not reason.strip() for reason in reasons
        ):
            raise ValueError(
                "metadata.supported_saturation_reasons must be a string array"
            )
        supported = frozenset(reason.strip() for reason in reasons)
        required = {"none", "physical_floor", "no_demand"}
        if not required <= supported:
            raise ValueError(
                "supported saturation reasons must include none, "
                "physical_floor, and no_demand"
            )
        return supported


def load_mock_round(
    graph: CampusGraph,
    scenario: str = "safe",
    path: str | Path = DEFAULT_MOCK_PATH,
) -> CampusGraph:
    """Load explicit demo-only upstream values for one scenario."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    base = raw.get("base")
    scenarios = raw.get("scenarios")
    if not isinstance(base, dict) or not isinstance(scenarios, dict):
        raise ValueError("Mock rounds require 'base' and 'scenarios' objects")
    if scenario not in scenarios or not isinstance(scenarios[scenario], dict):
        raise ValueError(f"Unknown mock scenario: {scenario}")

    combined = _merge_mock_sections(base, scenarios[scenario])
    node_properties = combined.get("node_properties", {})
    edge_properties = combined.get("edge_properties", {})
    actuator_properties = combined.get("actuator_properties", {})
    if not all(
        isinstance(section, dict)
        for section in (node_properties, edge_properties, actuator_properties)
    ):
        raise ValueError("Mock property sections must be objects")

    updated = graph
    for name, values in node_properties.items():
        if not isinstance(values, dict):
            raise ValueError(f"Mock node property '{name}' must be an object")
        updated = updated.with_node_property(name, values)
    for name, values in edge_properties.items():
        if not isinstance(values, dict):
            raise ValueError(f"Mock edge property '{name}' must be an object")
        updated = updated.with_edge_property(name, values)

    actuators = dict(updated.actuators)
    for actuator_id, properties in actuator_properties.items():
        if actuator_id not in actuators or not isinstance(properties, dict):
            raise ValueError(f"Invalid mock actuator entry: {actuator_id}")
        actuator = actuators[actuator_id]
        for name, value in properties.items():
            actuator = actuator.with_property(name, value)
        actuators[actuator_id] = actuator
    return replace(updated, actuators=actuators)


def _merge_mock_sections(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for section, section_values in override.items():
        if not isinstance(section_values, dict):
            merged[section] = section_values
            continue
        target = merged.setdefault(section, {})
        if not isinstance(target, dict):
            merged[section] = section_values
            continue
        for property_name, values in section_values.items():
            if isinstance(values, dict) and isinstance(target.get(property_name), dict):
                target[property_name].update(values)
            else:
                target[property_name] = values
    return merged


def run_demo() -> dict[str, ModelOutput]:
    model = VerificationModel.from_config()
    outputs: dict[str, ModelOutput] = {}
    expected = {
        "safe": "SAFE",
        "fallback": "INFEASIBLE_FALLBACK",
        "saturation": "SAFE",
        "invalid_locality": "INVALID",
    }
    for scenario, expected_outcome in expected.items():
        graph = load_mock_round(load_campus_graph(DEFAULT_DATASET_PATH), scenario)
        output = model.predict(ModelInput(graph=graph))
        if output.diagnostics["outcome"] != expected_outcome:
            raise AssertionError(
                f"{scenario}: expected {expected_outcome}, "
                f"got {output.diagnostics['outcome']}"
            )
        outputs[scenario] = output
    return outputs


if __name__ == "__main__":
    results = run_demo()
    print("\nRequirements verification demonstrations passed.")
    for name, result in results.items():
        diagnostics = result.diagnostics
        print(
            f"  {name}: {diagnostics['outcome']} "
            f"(usable={diagnostics['outcome'] != 'INVALID'})"
        )
