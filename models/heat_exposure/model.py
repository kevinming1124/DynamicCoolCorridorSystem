"""Per-edge heat exposure for the Dynamic Cool Corridors System.

The environment stage must run first. This stage reads air temperature,
humidity, open-sky solar radiation, and path shade from each edge, estimates
mean radiant temperature, calculates UTCI, and appends heat-exposure values to
the same edge.

The mean radiant temperature calculation is a deliberately simplified model.
It assumes the longwave radiant surroundings are at air temperature and adds
absorbed shortwave solar radiation using configurable body factors.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from utils import (
    CampusGraph,
    DynamicPropertyValue,
    GraphEdge,
    GraphNode,
    ModelInput,
    ModelOutput,
    StaticActuator,
    load_campus_graph,
)


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"
STEFAN_BOLTZMANN = 5.670374419e-8


@dataclass(frozen=True)
class ModelConfig:
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
        if raw["property_target"] != "edge":
            raise ValueError("The heat exposure model must target edges")

        output_properties = tuple(raw["output_properties"])
        input_properties = tuple(raw["input_properties"])
        if not output_properties or len(set(output_properties)) != len(output_properties):
            raise ValueError("'output_properties' must contain unique property names")
        if not input_properties or len(set(input_properties)) != len(input_properties):
            raise ValueError("'input_properties' must contain unique property names")

        parameters: dict[str, float] = {}
        for name, value in raw["parameters"].items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Parameter '{name}' must be a number")
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError(f"Parameter '{name}' must be finite")
            parameters[name] = numeric_value

        return cls(
            model_name=str(raw["model_name"]).strip(),
            description=str(raw["description"]),
            enabled=raw["enabled"],
            property_target="edge",
            output_properties=output_properties,
            input_properties=input_properties,
            parameters=parameters,
            metadata=raw.get("metadata", {}),
        )


class HeatExposureModel:
    """Convert environmental edge properties into heat-exposure properties."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._validate_parameters()

    @classmethod
    def from_config(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "HeatExposureModel":
        return cls(ModelConfig.from_file(path))

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError(f"Model '{self.config.model_name}' is disabled")

        entity_values = {
            edge.edge_id: self._validated_output_values(
                self.calculate_edge(edge, model_input.graph), edge.edge_id
            )
            for edge in model_input.graph.edges
        }
        values_by_property = {
            name: {edge_id: values[name] for edge_id, values in entity_values.items()}
            for name in self.config.output_properties
        }

        graph = model_input.graph
        for name, values in values_by_property.items():
            graph = graph.with_edge_property(name, values)

        return ModelOutput(
            model_name=self.config.model_name,
            property_target="edge",
            property_names=self.config.output_properties,
            property_values=values_by_property,
            graph=graph,
            diagnostics={
                "edge_count": len(graph.edges),
                "unsafe_utci_c": self._parameter("unsafe_utci_c"),
                "reference_utci_c": self._parameter("reference_utci_c"),
                "input_properties": list(self.config.input_properties),
                "assumed_wind_speed_10m_mps": self._parameter(
                    "assumed_wind_speed_10m_mps"
                ),
                "tmrt_method": "simplified absorbed shortwave with longwave baseline at air temperature",
            },
        )

    def calculate_edge(self, edge: GraphEdge, graph: CampusGraph) -> Mapping[str, float]:
        del graph
        inputs = self._numeric_input_values(edge.properties, edge.edge_id)
        air_temp_c = fahrenheit_to_celsius(inputs["air_temp_f"])
        tmrt_c = estimate_tmrt_c(
            air_temp_c=air_temp_c,
            solar_radiation_wm2=inputs["solar_radiation_wm2"],
            shade_pct=inputs["shade_pct"],
            shortwave_absorptivity=self._parameter("shortwave_absorptivity"),
            projected_area_factor=self._parameter("projected_area_factor"),
            body_emissivity=self._parameter("body_emissivity"),
        )
        utci_c = calculate_utci_c(
            air_temp_c=air_temp_c,
            tmrt_c=tmrt_c,
            wind_speed_10m_mps=self._parameter("assumed_wind_speed_10m_mps"),
            humidity_pct=inputs["humidity_pct"],
        )

        exposure_rate = max(0.0, utci_c - self._parameter("reference_utci_c"))
        walking_exposure = exposure_rate * edge.walk_time_min
        unsafe = 1.0 if utci_c >= self._parameter("unsafe_utci_c") else 0.0

        # Heat as if no mister were on, so Control can decide misting without
        # reacting to its own cooling. Shade and sunlight are unaffected by mist.
        if (inputs["uncooled_air_temp_f"] == inputs["air_temp_f"]
                and inputs["uncooled_humidity_pct"] == inputs["humidity_pct"]):
            uncooled_utci_c = utci_c
        else:
            uncooled_air_temp_c = fahrenheit_to_celsius(inputs["uncooled_air_temp_f"])
            uncooled_utci_c = calculate_utci_c(
                air_temp_c=uncooled_air_temp_c,
                tmrt_c=estimate_tmrt_c(
                    air_temp_c=uncooled_air_temp_c,
                    solar_radiation_wm2=inputs["solar_radiation_wm2"],
                    shade_pct=inputs["shade_pct"],
                    shortwave_absorptivity=self._parameter("shortwave_absorptivity"),
                    projected_area_factor=self._parameter("projected_area_factor"),
                    body_emissivity=self._parameter("body_emissivity"),
                ),
                wind_speed_10m_mps=self._parameter("assumed_wind_speed_10m_mps"),
                humidity_pct=inputs["uncooled_humidity_pct"],
            )

        return {
            "tmrt_c": round(tmrt_c, 2),
            "utci_c": round(utci_c, 2),
            "heat_exposure_rate": round(exposure_rate, 2),
            "walking_heat_exposure": round(walking_exposure, 2),
            "unsafe_heat": unsafe,
            "uncooled_utci_c": round(uncooled_utci_c, 2),
        }

    def calculate_node(self, node: GraphNode, graph: CampusGraph) -> Mapping[str, float]:
        raise NotImplementedError("The heat exposure model targets edges only")

    def calculate_actuator(
        self, actuator: StaticActuator, graph: CampusGraph
    ) -> Mapping[str, DynamicPropertyValue]:
        raise NotImplementedError("The heat exposure model targets edges only")

    def _validate_parameters(self) -> None:
        required = {
            "reference_utci_c",
            "unsafe_utci_c",
            "assumed_wind_speed_10m_mps",
            "shortwave_absorptivity",
            "projected_area_factor",
            "body_emissivity",
        }
        missing = required - self.config.parameters.keys()
        if missing:
            raise ValueError(f"Missing heat model parameters: {sorted(missing)}")
        if not 0.5 <= self._parameter("assumed_wind_speed_10m_mps") <= 17.0:
            raise ValueError("UTCI wind speed must be between 0.5 and 17 m/s")
        for name in ("shortwave_absorptivity", "projected_area_factor", "body_emissivity"):
            if not 0.0 < self._parameter(name) <= 1.0:
                raise ValueError(f"Parameter '{name}' must be in (0, 1]")

    def _validated_output_values(
        self, values: Mapping[str, float], entity_id: str
    ) -> dict[str, float]:
        expected = set(self.config.output_properties)
        if set(values) != expected:
            raise ValueError(f"{entity_id}: outputs do not match configured properties")
        validated = {}
        for name in self.config.output_properties:
            value = values[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{entity_id}: output '{name}' must be numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{entity_id}: output '{name}' must be finite")
            validated[name] = value
        return validated

    def _numeric_input_values(
        self, properties: Mapping[str, float], entity_id: str
    ) -> dict[str, float]:
        missing = [name for name in self.config.input_properties if name not in properties]
        if missing:
            raise ValueError(
                f"{entity_id}: environment properties are missing: {', '.join(missing)}. "
                "Run the environment model before the heat exposure model."
            )
        values = {}
        for name in self.config.input_properties:
            value = properties[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{entity_id}: input '{name}' must be numeric")
            values[name] = float(value)
        return values

    def _parameter(self, name: str) -> float:
        try:
            return self.config.parameters[name]
        except KeyError as exc:
            raise ValueError(f"Required parameter '{name}' is missing") from exc


def fahrenheit_to_celsius(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def estimate_tmrt_c(
    *,
    air_temp_c: float,
    solar_radiation_wm2: float,
    shade_pct: float,
    shortwave_absorptivity: float,
    projected_area_factor: float,
    body_emissivity: float,
) -> float:
    """Estimate MRT from air temperature and shade-adjusted shortwave radiation."""
    if not 0.0 <= shade_pct <= 100.0:
        raise ValueError("shade_pct must be between 0 and 100")
    if solar_radiation_wm2 < 0.0:
        raise ValueError("solar_radiation_wm2 cannot be negative")

    air_temp_k = air_temp_c + 273.15
    sun_fraction = 1.0 - shade_pct / 100.0
    effective_solar = solar_radiation_wm2 * sun_fraction
    absorbed_shortwave = (
        shortwave_absorptivity * projected_area_factor * effective_solar
    )
    tmrt_k = (
        air_temp_k**4
        + absorbed_shortwave / (body_emissivity * STEFAN_BOLTZMANN)
    ) ** 0.25
    return tmrt_k - 273.15


def calculate_utci_c(
    *,
    air_temp_c: float,
    tmrt_c: float,
    wind_speed_10m_mps: float,
    humidity_pct: float,
) -> float:
    try:
        from pythermalcomfort.models import utci
    except ImportError as exc:
        raise RuntimeError(
            "pythermalcomfort is required for UTCI. "
            "Install project dependencies with: python -m pip install -r requirements.txt"
        ) from exc

    result = utci(
        tdb=air_temp_c,
        tr=tmrt_c,
        v=wind_speed_10m_mps,
        rh=humidity_pct,
        units="SI",
        limit_inputs=True,
        round_output=False,
    )
    value = float(result.utci)
    if not math.isfinite(value):
        raise ValueError(
            "UTCI inputs are outside the model's valid range: "
            f"air_temp_c={air_temp_c:.2f}, tmrt_c={tmrt_c:.2f}, "
            f"wind_speed_10m_mps={wind_speed_10m_mps:.2f}, "
            f"humidity_pct={humidity_pct:.2f}"
        )
    return value


def _parse_arguments(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Run Environment then Heat Exposure and print a heat summary."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--time", default="13:00", help='Time of day, e.g. "08:00" (default: 13:00)')
    parser.add_argument("--scenario", default="hot", help="Weather scenario (default: hot)")
    parser.add_argument("--mister-on", nargs="*", default=[], metavar="ACTUATOR_ID",
                        help="Turn on these misters for this run, e.g. SIM_M1 SIM_M2")
    parser.add_argument("--save", type=Path, help="Save full heat output, including environmental edge properties, as JSON")
    return parser.parse_args(argv)


def run_standalone(
    dataset: str | Path = DEFAULT_DATASET_PATH,
    time: str = "13:00",
    scenario: str = "hot",
    mister_ids: tuple[str, ...] = (),
) -> ModelOutput:
    """Test heat exposure with Environment's real outputs on the selected map."""
    from models.environment import EnvironmentModel

    graph = load_campus_graph(dataset)
    invalid = [identifier for identifier in mister_ids
               if identifier not in graph.actuators
               or graph.actuators[identifier].actuator_type != "misting_point"]
    if invalid:
        raise ValueError(f"Unknown or non-misting actuator IDs: {invalid}")
    if mister_ids:
        graph = graph.with_actuator_property("status", {
            identifier: "on" if identifier in mister_ids else actuator.properties.get("status")
            for identifier, actuator in graph.actuators.items()
        })
    from utils.graph.mist_zones import split_edges_at_mist_zones

    environment = EnvironmentModel.from_config()
    graph, _ = split_edges_at_mist_zones(graph, environment.config.parameters["mist_range_m"])
    context = {"dataset_dir": str(dataset), "time": time, "scenario": scenario}
    environment_output = environment.predict(
        ModelInput(graph=graph, context=context)
    )
    output = HeatExposureModel.from_config().predict(
        environment_output.to_model_input(context=context)
    )
    return replace(output, diagnostics={
        **output.diagnostics,
        "dataset_dir": str(dataset),
        "time": environment_output.diagnostics["time"],
        "scenario": environment_output.diagnostics["scenario"],
        "misters_on": environment_output.diagnostics["misters_on"],
        "shade_source": environment_output.diagnostics["shade_source"],
        "edges_with_scheduled_shade": environment_output.diagnostics["edges_with_scheduled_shade"],
        "edges_with_auto_shade": environment_output.diagnostics["edges_with_auto_shade"],
    })


def main(argv=None) -> ModelOutput:
    arguments = _parse_arguments(argv)
    output = run_standalone(arguments.dataset, arguments.time, arguments.scenario,
                            tuple(arguments.mister_on))
    print(f"Dataset: {arguments.dataset}")
    print(f"Heat exposure at {output.diagnostics['time']}, "
          f"scenario '{output.diagnostics['scenario']}', {len(output.graph.edges)} edges, "
          f"misters on: {output.diagnostics['misters_on'] or 'none'}")
    for name in output.property_names:
        values = list(output.property_values[name].values())
        print(
            f"  {name:<24} min {min(values):8.2f}  "
            f"avg {sum(values) / len(values):8.2f}  max {max(values):8.2f}"
        )
    if arguments.save:
        print(f"Saved to {output.save_json(arguments.save)}")
    return output


if __name__ == "__main__":
    main()
