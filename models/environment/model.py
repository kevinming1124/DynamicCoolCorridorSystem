"""Environment model for the Dynamic Cool Corridors System.

Owner: Aman. Built from ``models/_template`` following the team workflow.

For one time of day and one weather scenario, this model writes seven
properties onto every edge of the campus graph:

    air_temp_f              air temperature after any misting cooling (F)
    humidity_pct            relative humidity after any misting increase (%)
    solar_radiation_wm2     open-sky sunlight at this time (W/m2)
    shade_pct               share of the path in shade right now (%)
    mist_cooling_f          cooling from nearby misters that are on (F)
    uncooled_air_temp_f     air temperature as if no mister were on (F)
    uncooled_humidity_pct   relative humidity as if no mister were on (%)
    local_temp_offset_f     this walkway's fixed offset from surface and
                            surroundings, already included in both air temps (F)

A mister cools an edge by its full strength times the share of the edge
inside its circle. The pipeline first cuts walkways at the circle boundaries
(``utils.graph.mist_zones``), so only the covered piece is cooled; see
``_mist_cooling_for``.

Inputs come from these places, so other models never call anything special:

    context["time"]        "HH:MM", defaults to config metadata default_time
    context["scenario"]    scenario name, defaults to default_scenario
    context["dataset_dir"] optional: folder of the loaded dataset, so its own
                           shade schedule is used (main.py passes it)
    misting actuators      their "status" ("on"/"off") on the graph

Shade works on any dataset. An edge listed in the dataset's shade schedule
uses that entry; every other edge gets a shade type worked out from its
direction and position (see ``_auto_shade_profiles``).

All tunable numbers live in config.json and scenarios.json.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from utils.graph.mist_zones import expand_split_ids, parent_edge_id
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
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"
MODEL_DIR = Path(__file__).parent

MIN_COVERAGE_RATIO = 1e-9

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
        self.shade_profiles = self._load_shade_profiles()
        self._dataset_schedules: dict[Path, dict[str, Any] | None] = {}
        self._dataset_walkways: dict[Path, list[list[str]]] = {}
        self._dataset_surroundings: dict[Path, dict[str, dict[str, Any]] | None] = {}
        self.local_rules = self._load_local_rules()
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
                "shade_source": self._state["shade_source"],
                "edges_with_scheduled_shade": self._state["scheduled_shade_edges"],
                "edges_with_auto_shade": self._state["auto_shade_edges"],
                "base_air_temp_f": self._state["base_air_temp_f"],
                "base_humidity_pct": self._state["base_humidity_pct"],
                "mist_range_m": self._parameter("mist_range_m"),
                "mist_humidity_increase_pct": self._parameter("mist_humidity_increase_pct"),
                "mist_cooling_rule": "strongest active mister's full cooling times the "
                                     "covered share of the edge",
                "local_offset_source": self._state["local_offset_source"],
                "local_offset_f": {
                    "min": min(self._state["local_offsets"].values(), default=0.0),
                    "max": max(self._state["local_offsets"].values(), default=0.0),
                    "nonzero_edges": sum(v != 0 for v in self._state["local_offsets"].values()),
                },
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
        local_offset = state["local_offsets"].get(edge.edge_id, 0.0)
        local_air_temp = state["base_air_temp_f"] + local_offset

        humidity = state["base_humidity_pct"]
        if mist_cooling > 0 and state["max_mist_reduction_f"] > 0:
            share = mist_cooling / state["max_mist_reduction_f"]
            humidity += self._parameter("mist_humidity_increase_pct") * share
        humidity = min(100.0, max(0.0, humidity))

        return {
            "air_temp_f": round(local_air_temp - mist_cooling, 2),
            "humidity_pct": round(humidity, 2),
            "solar_radiation_wm2": round(state["solar_radiation_wm2"], 2),
            "shade_pct": round(shade, 2),
            "mist_cooling_f": round(mist_cooling, 2),
            "uncooled_air_temp_f": round(local_air_temp, 2),
            "uncooled_humidity_pct": round(state["base_humidity_pct"], 2),
            "local_temp_offset_f": round(local_offset, 2),
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

        schedule, shade_source = self._shade_schedule_for(context.get("dataset_dir"))
        scheduled_edges = set(schedule["edges"]) if schedule else set()
        auto_edges = [
            edge for edge in model_input.graph.edges
            if parent_edge_id(edge.edge_id) not in scheduled_edges
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
            "shade_schedule": schedule,
            "shade_source": shade_source,
            "schedule_slot": (
                _latest_slot(schedule["time_slots"], hour) if schedule else None
            ),
            "auto_profiles": _auto_shade_profiles(
                model_input.graph, auto_edges,
                self._parameter("auto_shade_axis_ratio"),
                [expand_split_ids(group, model_input.graph)
                 for group in self._walkway_segments(context.get("dataset_dir"))],
            ),
            "scheduled_shade_edges": 0,
            "auto_shade_edges": 0,
        }
        offsets, offset_source = self._local_offsets(model_input.graph, context.get("dataset_dir"))
        self._state["local_offsets"] = offsets
        self._state["local_offset_source"] = offset_source

    def _shade_for(self, edge_id: str) -> float:
        """Shade percent for this edge right now.

        1. The edge is in the dataset's shade schedule: use that entry.
        2. Otherwise: use the shade type picked from the edge's direction
           and position, with the matching profile in config.json.
        """
        state = self._state
        schedule = state["shade_schedule"]
        scheduled_id = parent_edge_id(edge_id)  # cut pieces share their walkway's entry
        if schedule and scheduled_id in schedule["edges"]:
            state["scheduled_shade_edges"] += 1
            profile = schedule["profiles"][schedule["edges"][scheduled_id]]
            return float(profile[state["schedule_slot"]])

        state["auto_shade_edges"] += 1
        profile = self.shade_profiles[state["auto_profiles"][edge_id]]
        return float(profile[_latest_slot(list(profile), state["hour"])])

    def _mist_cooling_for(self, edge: GraphEdge, graph: CampusGraph) -> float:
        """Cooling from active misters on the covered share of this edge.

        Each mister gives ``max_heat_reduction_f * covered share``. After the
        pipeline cuts walkways at circle boundaries, an edge is either fully
        inside (full cooling) or fully outside (none). An uncut edge that is
        only partly covered gets the length-weighted average. When several
        misters cover an edge, the strongest contribution counts; misters do
        not stack.
        """
        if not self._state["misters_on"]:
            return 0.0
        start = graph.nodes[edge.from_node]
        end = graph.nodes[edge.to_node]
        mist_range = self._parameter("mist_range_m")
        return max(
            (
                (mister.max_heat_reduction_f or 0.0) * _segment_circle_coverage_ratio(
                    mister.x_m, mister.y_m, mist_range,
                    start.x_m, start.y_m, end.x_m, end.y_m
                )
                for mister in self._state["misters_on"]
            ),
            default=0.0,
        )

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

    # ------------------------------------------------------------------
    # Shade sources
    # ------------------------------------------------------------------
    def _shade_schedule_for(
        self, dataset_dir: Any
    ) -> tuple[dict[str, Any] | None, str]:
        """Pick the shade schedule for this graph and say where it came from.

        - dataset_dir given (main.py does this): that dataset's own schedule,
          the file named under "shade_schedule" in its dataset.json, if any.
        - No dataset_dir: generate shade from geometry and model profiles.
        Edges not covered by the chosen schedule get automatic shade.
        """
        if dataset_dir:
            schedule = self._dataset_schedule(Path(dataset_dir))
            if schedule is None:
                return None, "auto (dataset has no shade schedule)"
            return schedule, "dataset shade schedule + auto for unlisted edges"

        return None, "auto (no dataset schedule supplied)"

    def _dataset_schedule(self, dataset_dir: Path) -> dict[str, Any] | None:
        """The shade schedule named in <dataset_dir>/dataset.json, cached."""
        key = dataset_dir.resolve()
        if key not in self._dataset_schedules:
            manifest_path = key / "dataset.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ValueError(f"Dataset manifest not found: {manifest_path}") from exc
            name = manifest.get("files", {}).get("shade_schedule")
            self._dataset_schedules[key] = (
                _read_shade_schedule(key / name) if name else None
            )
        return self._dataset_schedules[key]

    def _walkway_segments(self, dataset_dir: Any) -> list[list[str]]:
        """Optional source-walkway grouping keeps shape segments on one profile."""
        if not dataset_dir:
            return []
        directory = Path(dataset_dir).resolve()
        if directory not in self._dataset_walkways:
            manifest = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
            filename = manifest.get("files", {}).get("source_edges")
            groups = []
            if filename:
                source = json.loads((directory / filename).read_text(encoding="utf-8"))
                groups = [record["pipeline_edges"] for record in source["source_edges"]]
            self._dataset_walkways[directory] = groups
        return self._dataset_walkways[directory]

    # ------------------------------------------------------------------
    # Local temperature offsets (surface and surroundings)
    # ------------------------------------------------------------------
    def _load_local_rules(self) -> dict[str, Any]:
        """Validate the surface and nearby-feature offset rules in config."""
        rules = self.config.metadata.get("local_temperature")
        if rules is None:
            return {"surface_offsets_f": {}, "nearby_rules": [],
                    "min_offset_f": 0.0, "max_offset_f": 0.0}

        def number(value, name):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise ValueError(f"local_temperature: '{name}' must be a finite number")
            return float(value)

        surface = {str(k): number(v, f"surface_offsets_f.{k}")
                   for k, v in rules.get("surface_offsets_f", {}).items()}
        nearby = []
        for rule in rules.get("nearby_rules", []):
            if not isinstance(rule, dict) or not isinstance(rule.get("feature"), str):
                raise ValueError("local_temperature: each nearby rule needs a 'feature' name")
            has_within, has_beyond = "within_m" in rule, "beyond_m" in rule
            if has_within == has_beyond:
                raise ValueError(f"local_temperature: rule '{rule.get('name', rule['feature'])}' "
                                 "needs exactly one of 'within_m' or 'beyond_m'")
            nearby.append({
                "name": str(rule.get("name", rule["feature"])),
                "feature": rule["feature"],
                "mode": "within" if has_within else "beyond",
                "distance_m": number(rule["within_m" if has_within else "beyond_m"], "distance"),
                "offset_f": number(rule.get("offset_f"), "offset_f"),
            })
        low = number(rules.get("min_offset_f", -3.0), "min_offset_f")
        high = number(rules.get("max_offset_f", 3.0), "max_offset_f")
        if low > high:
            raise ValueError("local_temperature: 'min_offset_f' cannot exceed 'max_offset_f'")
        return {"surface_offsets_f": surface, "nearby_rules": nearby,
                "min_offset_f": low, "max_offset_f": high}

    def local_offset_for(self, record: Mapping[str, Any]) -> float:
        """Offset (F) for one walkway's surroundings record, clamped to limits."""
        rules = self.local_rules
        offset = rules["surface_offsets_f"].get(record.get("surface", "unmatched"), 0.0)
        for rule in rules["nearby_rules"]:
            distance = record.get(rule["feature"])
            if rule["mode"] == "within":
                applies = distance is not None and distance <= rule["distance_m"]
            else:  # no such feature recorded nearby counts as far away
                applies = distance is None or distance > rule["distance_m"]
            if applies:
                offset += rule["offset_f"]
        return min(rules["max_offset_f"], max(rules["min_offset_f"], offset))

    def _local_offsets(self, graph: CampusGraph, dataset_dir: Any) -> tuple[dict[str, float], str]:
        """Per-edge offsets; cut mist-zone pieces use their walkway's record."""
        surroundings = self._dataset_surroundings_for(dataset_dir)
        if surroundings is None:
            return {}, "none (dataset has no surroundings file)"
        offsets = {}
        for edge in graph.edges:
            record = surroundings.get(parent_edge_id(edge.edge_id))
            offsets[edge.edge_id] = self.local_offset_for(record) if record is not None else 0.0
        return offsets, "dataset surroundings file"

    def _dataset_surroundings_for(self, dataset_dir: Any) -> dict[str, dict[str, Any]] | None:
        """The surroundings file named in <dataset_dir>/dataset.json, cached."""
        if not dataset_dir:
            return None
        directory = Path(dataset_dir).resolve()
        if directory not in self._dataset_surroundings:
            manifest = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
            filename = manifest.get("files", {}).get("surroundings")
            records = None
            if filename:
                raw = json.loads((directory / filename).read_text(encoding="utf-8"))
                records = raw.get("edges")
                if not isinstance(records, dict):
                    raise ValueError(f"{directory / filename}: 'edges' must be an object")
            self._dataset_surroundings[directory] = records
        return self._dataset_surroundings[directory]

    def _load_shade_profiles(self) -> dict[str, dict[str, float]]:
        """Dataset-independent shade curves used for automatic shade."""
        raw = self.config.metadata.get("shade_profiles")
        if not isinstance(raw, dict):
            raise ValueError("config.json metadata needs a 'shade_profiles' object")
        missing = set(AUTO_SHADE_TYPES) - raw.keys()
        if missing:
            raise ValueError(f"'shade_profiles' is missing {sorted(missing)}")
        profiles: dict[str, dict[str, float]] = {}
        for name, points in raw.items():
            if not isinstance(points, dict) or not points:
                raise ValueError(f"shade profile '{name}' must map 'HH:MM' to percent")
            ordered = sorted(points.items(), key=lambda item: _hour_from_text(item[0]))
            for slot, value in ordered:
                if not 0 <= float(value) <= 100:
                    raise ValueError(f"shade profile '{name}' at {slot} is not 0-100")
            profiles[name] = {slot: float(value) for slot, value in ordered}
        return profiles

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


AUTO_SHADE_TYPES = ("open", "tree_lined", "building_east", "building_west")


def _auto_shade_profiles(
    graph: CampusGraph, edges: list[GraphEdge], axis_ratio: float,
    walkway_segments: list[list[str]] | None = None,
) -> dict[str, str]:
    """Pick a shade type for each edge from its direction and position.

    Uses only node x/y positions, which every dataset has. When source walkway
    groups are supplied, classify each complete walkway once using its endpoints
    and share that profile across its segments. This also measures path spacing
    at the original walkway scale rather than at short geometry-vertex spacing.

    - North-south path (y changes at least axis_ratio times more than x):
      runs beside buildings. The building side alternates from one
      north-south path to the next, so neighbouring paths get morning
      (building_east) or afternoon (building_west) shade.
    - East-west path: every other one is tree_lined, the rest are open.
    - Diagonal: cuts across lawns and plazas, so it is open.

    "Next path" is measured in the dataset's own spacing: the typical
    length of its east-west edges (gap between north-south paths) and of
    its north-south edges (gap between east-west paths). On campus_50 this
    reproduces the generator's shade assignment exactly.
    """
    if not edges:
        return {}
    if walkway_segments:
        by_id = {edge.edge_id: edge for edge in graph.edges}
        grouped_ids: set[str] = set()
        representatives = []
        for group in walkway_segments:
            if not group or any(identifier not in by_id for identifier in group):
                raise ValueError("Source walkway must contain existing graph edge IDs")
            if len(set(group)) != len(group) or grouped_ids.intersection(group):
                raise ValueError("Source walkway segments must not repeat")
            chain = [by_id[identifier] for identifier in group]
            if any(a.to_node != b.from_node for a, b in zip(chain, chain[1:])):
                raise ValueError("Source walkway segments must form an ordered chain")
            grouped_ids.update(group)
            representatives.append(replace(chain[0], to_node=chain[-1].to_node))
        representatives.extend(edge for edge in graph.edges if edge.edge_id not in grouped_ids)
        walkway_graph = replace(graph, edges=tuple(representatives))
        profiles = _auto_shade_profiles(walkway_graph, representatives, axis_ratio)
        expanded = {identifier: profiles[group[0]] for group in walkway_segments for identifier in group}
        expanded.update({edge.edge_id: profiles[edge.edge_id]
                         for edge in graph.edges if edge.edge_id not in grouped_ids})
        return {edge.edge_id: expanded[edge.edge_id] for edge in edges}
    nodes = graph.nodes
    ratio = max(axis_ratio, 1.0)

    def direction(edge: GraphEdge) -> tuple[str, float, float]:
        a, b = nodes[edge.from_node], nodes[edge.to_node]
        dx, dy = abs(b.x_m - a.x_m), abs(b.y_m - a.y_m)
        if dy > 0 and dy >= ratio * dx:
            return "ns", dx, dy
        if dx > 0 and dx >= ratio * dy:
            return "ew", dx, dy
        return "diagonal", dx, dy

    kinds = {edge.edge_id: direction(edge) for edge in graph.edges}
    ew_lengths = [dx for kind, dx, _ in kinds.values() if kind == "ew"]
    ns_lengths = [dy for kind, _, dy in kinds.values() if kind == "ns"]
    all_lengths = ew_lengths + ns_lengths or [1.0]
    x_gap = _median(ew_lengths) if ew_lengths else _median(all_lengths)
    y_gap = _median(ns_lengths) if ns_lengths else _median(all_lengths)
    min_x = min(node.x_m for node in nodes.values())
    min_y = min(node.y_m for node in nodes.values())

    profiles: dict[str, str] = {}
    for edge in edges:
        kind = kinds[edge.edge_id][0]
        a, b = nodes[edge.from_node], nodes[edge.to_node]
        mid_x, mid_y = (a.x_m + b.x_m) / 2, (a.y_m + b.y_m) / 2
        if kind == "ns":
            column = math.floor((mid_x - min_x) / x_gap + 0.5)
            profiles[edge.edge_id] = "building_east" if column % 2 == 0 else "building_west"
        elif kind == "ew":
            row = math.floor((mid_y - min_y) / y_gap + 0.5)
            profiles[edge.edge_id] = "tree_lined" if row % 2 == 1 else "open"
        else:
            profiles[edge.edge_id] = "open"
    return profiles


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _latest_slot(slots: list[str], hour: float) -> str:
    """Latest "HH:MM" slot at or before this hour; the first slot if none."""
    chosen = slots[0]
    for slot in slots:
        if _hour_from_text(slot) <= hour + 1e-9:
            chosen = slot
    return chosen


def _read_shade_schedule(path: Path) -> dict[str, Any]:
    """Read a dataset shade schedule and check its shape."""
    try:
        schedule = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Shade schedule not found: {path}") from exc
    for key in ("time_slots", "profiles", "edges"):
        if key not in schedule:
            raise ValueError(f"{path}: shade schedule is missing '{key}'")
    for edge_id, profile_name in schedule["edges"].items():
        if profile_name not in schedule["profiles"]:
            raise ValueError(f"{path}: {edge_id} uses unknown profile '{profile_name}'")
    for name, profile in schedule["profiles"].items():
        missing = [slot for slot in schedule["time_slots"] if slot not in profile]
        if missing:
            raise ValueError(f"{path}: profile '{name}' is missing slots {missing}")
    return schedule


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


def _segment_circle_coverage_ratio(
    px: float, py: float, radius: float,
    ax: float, ay: float, bx: float, by: float
) -> float:
    """Fraction of straight segment A-B inside the circle centered at P.

    The dimensionless fraction uses coordinate length, not travel distance.
    Tangencies, zero-length segments, and nonpositive radii cover no length.
    """
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if radius <= 0 or length == 0:
        return 0.0
    ux, uy = dx / length, dy / length
    projection = (px - ax) * ux + (py - ay) * uy
    perpendicular = (px - ax) * uy - (py - ay) * ux
    half_chord_squared = radius * radius - perpendicular * perpendicular
    if half_chord_squared <= 0:
        return 0.0
    half_chord = math.sqrt(half_chord_squared)
    covered = max(0.0, min(length, projection + half_chord)
                  - max(0.0, projection - half_chord))
    ratio = min(1.0, covered / length)
    # A piece cut exactly at the circle boundary can show a rounding-error
    # sliver of coverage; treat it as outside.
    return 0.0 if ratio < MIN_COVERAGE_RATIO else ratio


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
    from utils.graph.mist_zones import split_edges_at_mist_zones

    graph = _graph_with_misters_on(
        load_campus_graph(arguments.dataset), arguments.mister_on
    )
    graph, _ = split_edges_at_mist_zones(graph, model.config.parameters["mist_range_m"])
    context = {"dataset_dir": str(arguments.dataset)}
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
