"""Control the existing state of campus actuators."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from models.environment.model import (
    ModelConfig as EnvironmentConfig,
    _segment_circle_coverage_ratio,
)

from utils import (
    CampusGraph,
    StaticActuator,
    ModelInput,
    ModelOutput,
    load_campus_graph,
)


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"

UNSAFE_HEAT_PROPERTY = "unsafe_heat"


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

    def __init__(self, config: ModelConfig, mist_range_m: float | None = None) -> None:
        self.config = config
        self.mist_range_m = (EnvironmentConfig.from_file().parameters["mist_range_m"]
                             if mist_range_m is None else mist_range_m)
        if not math.isfinite(self.mist_range_m) or self.mist_range_m <= 0:
            raise ValueError("Misting observation radius must be finite and positive")

    @classmethod
    def from_config(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "ControlModel":
        return cls(ModelConfig.from_file(path))

    def control(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        actuators = {
            actuator_id: (actuator if actuator.actuator_type == "directional_led"
                          else self.control_actuator(actuator, model_input.graph))
            for actuator_id, actuator in model_input.graph.actuators.items()
        }
        graph = replace(model_input.graph, actuators=actuators)

        return ModelOutput(
            model_name=self.config.model_name,
            property_target=None,
            property_names=(),
            property_values={},
            graph=graph,
            diagnostics={"mist_range_m": self.mist_range_m},
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

        raise ValueError(
            f"{actuator.actuator_id}: unsupported actuator type "
            f"'{actuator.actuator_type}'"
        )

    def control_misting_point(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Mist when any segment overlapping the cooling zone is unsafe."""
        observed_edges = tuple(
            edge
            for edge in graph.edges
            if _segment_circle_coverage_ratio(
                actuator.x_m, actuator.y_m, self.mist_range_m,
                graph.nodes[edge.from_node].x_m, graph.nodes[edge.from_node].y_m,
                graph.nodes[edge.to_node].x_m, graph.nodes[edge.to_node].y_m,
            ) > 0
        )
        if not observed_edges:
            raise ValueError(
                f"{actuator.actuator_id}: no walkway segments overlap the misting observation zone"
            )
        unsafe_heat = max(
            self._unsafe_heat(edge.properties, edge.edge_id)
            for edge in observed_edges
        )
        status = "on" if unsafe_heat == 1.0 else "off"
        print(
            f"Controlling misting point {actuator.actuator_id}: "
            f"unsafe heat {unsafe_heat:.0f} "
            f"-> {status}"
        )
        return actuator.with_property("status", status)

    def control_traffic_signal(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> StaticActuator:
        """Reduce wait time when any controlled edge has unsafe heat."""
        unsafe_heat = max(
            self._edge_property(graph, edge_id, UNSAFE_HEAT_PROPERTY)
            for edge_id in actuator.controlled_edges
        )
        if actuator.min_wait_time_s is None or actuator.default_wait_time_s is None:
            raise ValueError(
                f"{actuator.actuator_id}: traffic signal wait-time limits are missing"
            )
        wait_time_s = (
            actuator.min_wait_time_s
            if unsafe_heat == 1.0
            else actuator.default_wait_time_s
        )
        print(
            f"Controlling traffic signal {actuator.actuator_id}: "
            f"unsafe heat {unsafe_heat:.0f} -> wait {wait_time_s:.0f} s"
        )
        return actuator.with_property("status", "on").with_property(
            "wait_time_s", wait_time_s
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
        if property_name == UNSAFE_HEAT_PROPERTY:
            return self._unsafe_heat(edge.properties, edge.edge_id)
        return self._numeric_property(edge.properties, property_name, edge.edge_id)

    @staticmethod
    def _unsafe_heat(properties: Mapping[str, object], entity_id: str) -> float:
        value = ControlModel._numeric_property(
            properties, UNSAFE_HEAT_PROPERTY, entity_id
        )
        if value not in (0.0, 1.0):
            raise ValueError(
                f"{entity_id}: dynamic property '{UNSAFE_HEAT_PROPERTY}' "
                "must be 0.0 or 1.0"
            )
        return value

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


def audit_control_rules(before: CampusGraph, after: CampusGraph, radius: float) -> list[dict]:
    """Check commands against pre-control heat flags, without applying effects.

    Observation is checked independently using point-to-segment distance rather
    than the controller's circle-intersection coverage calculation.
    """
    reports = []
    for identifier, actuator in before.actuators.items():
        if actuator.actuator_type == "misting_point":
            observed = []
            for edge in before.edges:
                a, b = before.nodes[edge.from_node], before.nodes[edge.to_node]
                dx, dy = b.x_m - a.x_m, b.y_m - a.y_m
                squared = dx * dx + dy * dy
                if squared == 0:
                    continue
                t = max(0.0, min(1.0, ((actuator.x_m - a.x_m) * dx
                                      + (actuator.y_m - a.y_m) * dy) / squared))
                distance = math.hypot(a.x_m + t * dx - actuator.x_m,
                                      a.y_m + t * dy - actuator.y_m)
                if distance < radius:
                    observed.append(edge)
        elif actuator.actuator_type == "traffic_signal":
            observed = [edge for edge in before.edges if edge.edge_id in actuator.controlled_edges]
        else:
            continue
        unsafe_count = sum(edge.properties[UNSAFE_HEAT_PROPERTY] == 1.0 for edge in observed)
        if actuator.actuator_type == "misting_point":
            expected = {"status": "on" if unsafe_count else "off"}
        else:
            expected = {"status": "on", "wait_time_s": (
                actuator.min_wait_time_s if unsafe_count else actuator.default_wait_time_s)}
        actual = {key: after.actuators[identifier].properties.get(key) for key in expected}
        previous = {key: actuator.properties.get(key) for key in expected}
        reports.append({
            "actuator_id": identifier, "actuator_type": actuator.actuator_type,
            "observed_edges": [edge.edge_id for edge in observed],
            "unsafe_segment_count": unsafe_count,
            "previous": previous, "expected": expected, "actual": actual,
            "result": "PASS" if observed and actual == expected else "FAIL",
        })
    return reports


def run_standalone(
    dataset: str | Path = DEFAULT_DATASET_PATH,
    time: str = "13:00",
    scenario: str = "hot",
    misting_count: int | None = 4,
) -> ModelOutput:
    """Run upstream inputs once and check one control decision, without effects."""
    from models.environment import EnvironmentModel
    from models.heat_exposure import HeatExposureModel
    from utils.graph.misting_scenarios import apply_misting_scenario

    dataset = Path(dataset)
    environment = EnvironmentModel.from_config()
    radius = environment.config.parameters["mist_range_m"]
    model = ControlModel(ModelConfig.from_file(), mist_range_m=radius)
    graph = load_campus_graph(dataset)
    layout = None
    if misting_count is not None:
        graph, layout = apply_misting_scenario(graph, dataset, misting_count, radius)
    context = {"dataset_dir": str(dataset), "time": time, "scenario": scenario}
    environment_output = environment.predict(ModelInput(graph=graph, context=context))
    heat_output = HeatExposureModel.from_config().predict(
        environment_output.to_model_input(context=context)
    )
    output = model.control(heat_output.to_model_input(context=context))
    reports = audit_control_rules(heat_output.graph, output.graph, radius)
    return replace(output, diagnostics={
        **output.diagnostics,
        "dataset_dir": str(dataset), "time": time, "scenario": scenario,
        "misting_count": sum(a.actuator_type == "misting_point" for a in graph.actuators.values()),
        "misting_layout": layout,
        "shade_source": environment_output.diagnostics["shade_source"],
        "rule_checks": reports,
        "rule_check_status": "PASS" if all(r["result"] == "PASS" for r in reports) else "FAIL",
        "heat_snapshot": "pre-control; actuator effects are not recalculated",
    })


def main(argv=None) -> ModelOutput:
    import argparse
    from utils.graph.misting_scenarios import MISTING_COUNTS

    parser = argparse.ArgumentParser(description="Check one control decision using real upstream heat inputs.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--time", default="13:00")
    parser.add_argument("--scenario", default="hot")
    parser.add_argument("--misting-count", choices=[str(n) for n in MISTING_COUNTS] + ["base"],
                        default="4", help="Mister layout: 0, 4 (default), 6, 10, or base for dataset actuators")
    parser.add_argument("--save", type=Path, help="Save commands, pre-control heat inputs, and rule checks as JSON")
    arguments = parser.parse_args(argv)
    count = None if arguments.misting_count == "base" else int(arguments.misting_count)
    output = run_standalone(arguments.dataset, arguments.time, arguments.scenario, count)
    print(f"Dataset: {arguments.dataset}; misters: {output.diagnostics['misting_count']}; "
          f"time: {arguments.time}; scenario: {arguments.scenario}")
    print("One control decision; heat values describe conditions before these commands.")
    for report in output.diagnostics["rule_checks"]:
        print(f"{report['actuator_id']}: observed {len(report['observed_edges'])} segments, "
              f"{report['unsafe_segment_count']} unsafe; previous {report['previous']}; "
              f"expected {report['expected']}; actual {report['actual']} -> {report['result']}")
        print(f"  Observed segments: {', '.join(report['observed_edges'])}")
    print(f"Control rules: {output.diagnostics['rule_check_status']}")
    if arguments.save:
        print(f"Saved to {output.save_json(arguments.save)}")
    if output.diagnostics["rule_check_status"] != "PASS":
        raise SystemExit(1)
    return output


if __name__ == "__main__":
    main()
