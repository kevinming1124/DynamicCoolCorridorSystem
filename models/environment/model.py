"""Environment model for the Dynamic Cool Corridors System.

Owner: Aman. Built from ``models/_template`` following the team workflow.

For one time of day and one weather scenario, this model writes five
properties onto every edge of the campus graph:

    air_temp_f            air temperature after any misting cooling (F)
    humidity_pct          relative humidity after any misting increase (%)
    solar_radiation_wm2   open-sky sunlight at this time (W/m2)
    shade_pct             share of the path in shade, from the shade schedule (%)
    mist_cooling_f        cooling from nearby misters that are on (F)

Inputs come from three places, so other models never call anything special:

    context["time"]       "HH:MM", defaults to config metadata default_time
    context["scenario"]   scenario name, defaults to default_scenario
    misting actuators     their "status" ("on"/"off") on the graph

All tunable numbers live in config.json and scenarios.json.
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
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "campus_50"
MODEL_DIR = Path(__file__).parent

REQUIRED_SCENARIO_FIELDS = (
    "temp_low_f",
    "temp_high_f",
    "humidity_high_pct",
    "humidity_low_pct",
    "cloud_factor",
)


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
        if not isinstance(raw_output_properties, list):
            raise ValueError("'output_properties' must be a JSON array")
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


class EnvironmentModel:
    """Per-edge environmental conditions for one time of day."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.scenarios = self._load_scenarios()
        self.shade_schedule = self._load_shade_schedule()
        self._state: dict[str, Any] = {}

    @classmethod
    def from_config(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "EnvironmentModel":
        return cls(ModelConfig.from_file(path))

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        self._prepare(model_input)

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
                "time": self._state["time"],
                "scenario": self._state["scenario_name"],
                "misters_on": sorted(self._state["misters_on_ids"]),
                "edges_using_default_shade": self._state["default_shade_edges"],
            },
        )

    def calculate_edge(
        self, edge: GraphEdge, graph: CampusGraph
    ) -> Mapping[str, float]:
        """Calculate the five environmental properties for one edge.

        HUMAN-EDITABLE SECTION
        ----------------------
        Values shared by every edge (temperature curve, humidity curve,
        sunlight) are computed once per run in ``_prepare``. This method adds
        the edge-specific parts: shade from the schedule and misting cooling.
        """
        state = self._state
        shade = self._shade_for(edge.edge_id)
        mist_cooling = self._mist_cooling_for(edge, graph)

        humidity = state["base_humidity_pct"]
        if mist_cooling > 0 and state["max_mist_reduction_f"] > 0:
            share = mist_cooling / state["max_mist_reduction_f"]
            humidity += self._parameter("mist_humidity_increase_pct") * share
        humidity = min(100.0, max(0.0, humidity))

        return {
            "air_temp_f": round(state["base_air_temp_f"] - mist_cooling, 2),
            "humidity_pct": round(humidity, 2),
            "solar_radiation_wm2": round(state["solar_radiation_wm2"], 2),
            "shade_pct": round(shade, 2),
            "mist_cooling_f": round(mist_cooling, 2),
        }

    # ------------------------------------------------------------------
    # Environment calculations
    # ------------------------------------------------------------------
    def _prepare(self, model_input: ModelInput) -> None:
        """Work out everything that is the same for every edge in this run."""
        metadata = self.config.metadata
        context = model_input.context or {}
        time_text = str(context.get("time", metadata.get("default_time", "12:00")))
        scenario_name = str(
            context.get("scenario", metadata.get("default_scenario", "hot"))
        )
        if scenario_name not in self.scenarios:
            raise ValueError(
                f"Unknown scenario '{scenario_name}'. "
                f"Available: {', '.join(sorted(self.scenarios))}"
            )
        scenario = self.scenarios[scenario_name]
        hour = _hour_from_text(time_text)

        # Daily curve: 0 at the daily low (12 h before the peak), 1 at the peak.
        peak = self._parameter("temp_peak_hour")
        heat_share = (1 + math.cos(math.pi * (hour - peak) / 12)) / 2

        air_temp = scenario["temp_low_f"] + (
            scenario["temp_high_f"] - scenario["temp_low_f"]
        ) * heat_share
        humidity = scenario["humidity_high_pct"] - (
            scenario["humidity_high_pct"] - scenario["humidity_low_pct"]
        ) * heat_share

        sunrise = self._parameter("sunrise_hour")
        sunset = self._parameter("sunset_hour")
        if sunrise < hour < sunset:
            sun_share = math.sin(math.pi * (hour - sunrise) / (sunset - sunrise))
        else:
            sun_share = 0.0
        solar = (
            self._parameter("peak_solar_radiation_wm2")
            * sun_share
            * scenario["cloud_factor"]
        )

        misters_on = [
            actuator
            for actuator in model_input.graph.actuators.values()
            if actuator.actuator_type == "misting_point"
            and actuator.properties.get("status") == "on"
        ]

        self._state = {
            "time": time_text,
            "hour": hour,
            "scenario_name": scenario_name,
            "base_air_temp_f": air_temp,
            "base_humidity_pct": humidity,
            "solar_radiation_wm2": solar,
            "misters_on": misters_on,
            "misters_on_ids": [a.actuator_id for a in misters_on],
            "max_mist_reduction_f": max(
                (a.max_heat_reduction_f or 0.0 for a in misters_on), default=0.0
            ),
            "shade_slot": self._shade_slot(hour),
            "default_shade_edges": 0,
        }

    def _shade_for(self, edge_id: str) -> float:
        """Shade percent for this edge at the current time slot."""
        schedule = self.shade_schedule
        profile_name = schedule.get("edges", {}).get(edge_id) if schedule else None
        if profile_name is None:
            self._state["default_shade_edges"] += 1
            return self._parameter("default_shade_pct")
        profile = schedule["profiles"][profile_name]
        return float(profile[self._state["shade_slot"]])

    def _mist_cooling_for(self, edge: GraphEdge, graph: CampusGraph) -> float:
        """Cooling from misters that are on, fading to zero at mist_range_m.

        Distance is measured from the mister to the closest point on the edge.
        Overlapping misters add up but never exceed the strongest mister's limit.
        """
        if not self._state["misters_on"]:
            return 0.0
        start = graph.nodes[edge.from_node]
        end = graph.nodes[edge.to_node]
        mist_range = self._parameter("mist_range_m")
        total = 0.0
        for mister in self._state["misters_on"]:
            distance = _point_to_segment_m(
                mister.x_m, mister.y_m, start.x_m, start.y_m, end.x_m, end.y_m
            )
            if distance < mist_range:
                total += (mister.max_heat_reduction_f or 0.0) * (
                    1 - distance / mist_range
                )
        return min(total, self._state["max_mist_reduction_f"])

    def _shade_slot(self, hour: float) -> str | None:
        """Latest schedule slot at or before this time, clamped to the day."""
        if not self.shade_schedule:
            return None
        slots = self.shade_schedule["time_slots"]
        chosen = slots[0]
        for slot in slots:
            if _hour_from_text(slot) <= hour + 1e-9:
                chosen = slot
        return chosen

    def _load_scenarios(self) -> dict[str, dict[str, float]]:
        name = self.config.metadata.get("scenarios_file", "scenarios.json")
        path = MODEL_DIR / name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Scenarios file not found: {path}") from exc
        scenarios = raw.get("scenarios")
        if not isinstance(scenarios, dict) or not scenarios:
            raise ValueError(f"{path}: 'scenarios' must be a non-empty object")
        cleaned: dict[str, dict[str, float]] = {}
        for scenario_name, values in scenarios.items():
            missing = [f for f in REQUIRED_SCENARIO_FIELDS if f not in values]
            if missing:
                raise ValueError(
                    f"{path}: scenario '{scenario_name}' is missing {missing}"
                )
            cleaned[scenario_name] = {
                f: float(values[f]) for f in REQUIRED_SCENARIO_FIELDS
            }
        return cleaned

    def _load_shade_schedule(self) -> dict[str, Any] | None:
        """Load the shade schedule; return None if it does not exist.

        Without a schedule every edge uses default_shade_pct, so the model
        still runs on datasets such as tiny_campus_block.
        """
        name = self.config.metadata.get("shade_schedule_file")
        if not name:
            return None
        path = PROJECT_ROOT / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def calculate_node(
        self, node: GraphNode, graph: CampusGraph
    ) -> Mapping[str, float]:
        """Not used: this model only writes edge properties."""
        raise NotImplementedError("The environment model targets edges only")

    def calculate_actuator(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> Mapping[str, DynamicPropertyValue]:
        """Not used: misters are read, not changed, by this model."""
        raise NotImplementedError("The environment model targets edges only")

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


def _hour_from_text(time_text: str) -> float:
    """Convert "HH:MM" to hours, e.g. "13:30" -> 13.5."""
    try:
        hours_text, minutes_text = time_text.strip().split(":")
        hours, minutes = int(hours_text), int(minutes_text)
    except ValueError as exc:
        raise ValueError(f"Time must look like 'HH:MM', got '{time_text}'") from exc
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Time out of range: '{time_text}'")
    return hours + minutes / 60


def _point_to_segment_m(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Shortest distance in meters from point P to the segment A-B."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _parse_arguments():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the environment model on its own and print a summary."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--time", default=None, help='e.g. "13:00"')
    parser.add_argument("--scenario", default=None, help="e.g. hot, mild")
    parser.add_argument(
        "--mister-on",
        nargs="*",
        default=[],
        metavar="ACTUATOR_ID",
        help="Turn these misters on for this run, e.g. A01 A02",
    )
    parser.add_argument(
        "--day",
        action="store_true",
        help="Print campus averages for every 30-minute step, 08:00-18:00",
    )
    parser.add_argument("--save", type=Path, help="Save the full output as JSON")
    return parser.parse_args()


def _graph_with_misters_on(graph: CampusGraph, mister_ids: list[str]) -> CampusGraph:
    """Test helper: set chosen misters to "on" the same way a controller would."""
    if not mister_ids:
        return graph
    unknown = [m for m in mister_ids if m not in graph.actuators]
    if unknown:
        raise ValueError(f"Unknown actuator IDs: {unknown}")
    status = {
        actuator_id: (
            "on" if actuator_id in mister_ids else actuator.properties.get("status")
        )
        for actuator_id, actuator in graph.actuators.items()
    }
    return graph.with_actuator_property("status", status)


def _summary(output: ModelOutput) -> str:
    lines = []
    for name in output.property_names:
        values = list(output.property_values[name].values())
        lines.append(
            f"  {name:<20} min {min(values):8.2f}   "
            f"avg {sum(values) / len(values):8.2f}   max {max(values):8.2f}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    arguments = _parse_arguments()
    model = EnvironmentModel.from_config()
    graph = _graph_with_misters_on(
        load_campus_graph(arguments.dataset), arguments.mister_on
    )
    context = {}
    if arguments.scenario:
        context["scenario"] = arguments.scenario

    if arguments.day:
        slots = [f"{m // 60:02d}:{m % 60:02d}" for m in range(8 * 60, 18 * 60 + 1, 30)]
        print(f"Scenario: {context.get('scenario', model.config.metadata['default_scenario'])}")
        print(" time   air_temp_f  humidity_pct  solar_wm2  shade_pct")
        for slot in slots:
            out = model.predict(ModelInput(graph=graph, context={**context, "time": slot}))
            avg = {
                n: sum(out.property_values[n].values()) / len(out.property_values[n])
                for n in out.property_names
            }
            print(
                f" {slot}  {avg['air_temp_f']:10.1f}  {avg['humidity_pct']:12.1f}"
                f"  {avg['solar_radiation_wm2']:9.0f}  {avg['shade_pct']:9.1f}"
            )
    else:
        if arguments.time:
            context["time"] = arguments.time
        output = model.predict(ModelInput(graph=graph, context=context))
        d = output.diagnostics
        print(
            f"Environment at {d['time']}, scenario '{d['scenario']}', "
            f"{d['edge_count']} edges, misters on: {d['misters_on'] or 'none'}"
        )
        print(_summary(output))
        if arguments.save:
            print(f"Saved to {output.save_json(arguments.save)}")
