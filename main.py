"""Run the Dynamic Cool Corridor System model pipeline."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from models.control import ControlModel
from models.environment import EnvironmentModel
from models.environment.model import ModelConfig as EnvironmentConfig
from models.environment.model import ModelConfig as EnvironmentConfig
from models.heat_exposure import HeatExposureModel
from models.pedestrian.model import PedestrianSimulator
from models.routing import RoutingModel, TripRequest, load_trip_requests
from models.verification import VerificationModel
from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph
from utils.graph.misting_scenarios import MISTING_COUNTS, apply_misting_scenario


PROJECT_ROOT = Path(__file__).parent
DEFAULT_DATASET = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"


class PipelineStage(Protocol):
    """Minimum interface required for a pipeline model."""

    def predict(self, model_input: ModelInput) -> ModelOutput:
        ...


def dataset_metadata(directory: Path) -> dict:
    return json.loads((directory / "dataset.json").read_text(encoding="utf-8"))


def build_initialization_pipeline(dataset_directory: Path | None = None) -> list[PipelineStage]:
    """Environment reads the selected map's schedule from dataset_dir in context."""
    return [EnvironmentModel.from_config(), HeatExposureModel.from_config()]


def build_pipeline(pedestrians: PedestrianSimulator,
                   trip_requests: Sequence[TripRequest] | None = None,
                   dataset_directory: Path | None = None) -> list[PipelineStage]:
    """Create routing and movement once, preserving population across rounds."""
    buildings = None
    if dataset_directory is not None:
        filename = dataset_metadata(dataset_directory).get("files", {}).get("buildings")
        if filename:
            buildings = json.loads((dataset_directory / filename).read_text(encoding="utf-8"))
    return [ControlModel.from_config(),
            *build_initialization_pipeline(dataset_directory),
            RoutingModel.from_config(pedestrians, requests=trip_requests, buildings=buildings),
            pedestrians, VerificationModel.from_config()]


def run_pipeline(
    graph: CampusGraph,
    stages: Sequence[PipelineStage],
    debug_directory: str | Path | None = None,
    context: Mapping[str, Any] | None = None,
) -> ModelOutput:
    """Run every stage and return the final output."""
    if not stages:
        raise ValueError("The pipeline must contain at least one model stage")

    # Evidence is scoped to this pass; never reuse a previous round's reports.
    runtime_context = {**(context or {}), "stage_diagnostics": {}}
    model_input = ModelInput(graph=graph, context=runtime_context)
    final_output: ModelOutput | None = None

    for stage_number, stage in enumerate(stages, start=1):
        if isinstance(stage, PedestrianSimulator):
            runtime_context["pedestrian_before_movement"] = stage.get_state()
        final_output = stage.predict(model_input)
        if "misting_scenario" in runtime_context:
            final_output = replace(final_output, diagnostics={
                **final_output.diagnostics,
                "misting_scenario": runtime_context["misting_scenario"],
            })
        runtime_context["stage_diagnostics"][final_output.model_name] = final_output.diagnostics
        if debug_directory is not None:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", final_output.model_name)
            debug_path = Path(debug_directory) / f"{stage_number:02d}_{safe_name}.json"
            final_output.save_json(debug_path)
        model_input = final_output.to_model_input(context=runtime_context)

    assert final_output is not None
    return final_output


def run_simulation(
    graph: CampusGraph,
    rounds: int = 1,
    debug_directory: str | Path | None = None,
    context: Mapping[str, Any] | None = None,
    pedestrians: PedestrianSimulator | None = None,
    trip_requests: Sequence[TripRequest] | None = None,
    dataset_directory: Path | None = None,
) -> ModelOutput:
    """Initialize once, then carry the graph through exactly ``rounds`` rounds.

    Time and scenario remain fixed. The final heat values reflect the final
    round's actuator commands; control is not rerun after those calculations.
    """
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("Rounds must be a positive integer")
    context = dict(context or {})
    if dataset_directory is not None:
        context["dataset_dir"] = str(dataset_directory)
    debug_root = Path(debug_directory) if debug_directory is not None else None
    print("Initializing: Environment -> Heat Exposure")
    output = run_pipeline(
        graph=graph,
        stages=build_initialization_pipeline(dataset_directory),
        debug_directory=debug_root / "initialization" if debug_root is not None else None,
        context=context,
    )
    # Build the stage list once; never recreate agents inside the round loop.
    simulator = pedestrians if pedestrians is not None else PedestrianSimulator(output.graph)
    stages = build_pipeline(simulator, trip_requests, dataset_directory)
    for round_number in range(1, rounds + 1):
        print(
            f"Round {round_number}/{rounds}: "
            "Control -> Environment -> Heat Exposure -> Routing -> Pedestrian -> Verification"
        )
        output = run_pipeline(
            graph=output.graph,
            stages=stages,
            debug_directory=(
                debug_root / f"round_{round_number:03d}" if debug_root is not None else None
            ),
            context=context,
        )
    return output


def positive_rounds(value: str) -> int:
    try:
        rounds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("rounds must be a positive integer") from exc
    if rounds < 1:
        raise argparse.ArgumentTypeError("rounds must be a positive integer")
    return rounds


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Directory containing dataset.json and its referenced JSON files",
    )
    parser.add_argument(
        "--misting-count", type=int, choices=MISTING_COUNTS,
        help="Select a nested misting layout (0, 4, 6, 10); requires the dataset's "
             "misting_scenarios.json. Omit to use original dataset actuators.",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        help="Save the complete output from every stage as JSON",
    )
    parser.add_argument(
        "--rounds",
        type=positive_rounds,
        default=1,
        help="Number of control/environment/heat/routing/pedestrian rounds after initialization (default: 1)",
    )
    parser.add_argument(
        "--time",
        default="12:00",
        help='Simulation time in HH:MM format (default: "12:00")',
    )
    parser.add_argument(
        "--scenario",
        default="hot",
        help="Environment scenario (default: hot)",
    )
    demand = parser.add_mutually_exclusive_group()
    demand.add_argument("--trips", type=Path,
                        help="Trip requests with pedestrian IDs, origin/destination and comfort preference")
    demand.add_argument(
        "--routes",
        type=Path,
        help="Legacy route JSON: use endpoints as trip requests and recompute paths with routingc",
    )
    parser.add_argument(
        "--pedestrian-dt",
        type=float,
        default=1.0,
        help="Movement seconds per round (default: 1.0; zero reports occupancy only)",
    )
    return parser.parse_args(argv)


def main() -> None:
    arguments = parse_arguments()
    graph = load_campus_graph(arguments.dataset)
    misting_scenario = None
    if arguments.misting_count is not None:
        graph, misting_scenario = apply_misting_scenario(
            graph, arguments.dataset, arguments.misting_count,
            EnvironmentConfig.from_file().parameters["mist_range_m"],
        )
    pedestrians = PedestrianSimulator(graph)
    trip_file = arguments.trips or arguments.routes
    requests = load_trip_requests(trip_file) if trip_file else None
    metadata = dataset_metadata(arguments.dataset)
    print(f"Dataset: {metadata.get('dataset_id', arguments.dataset.name)}; "
          f"{len(graph.nodes)} nodes, {len(graph.edges)} edges")
    for assumption in metadata.get("assumptions", []):
        if misting_scenario is not None and assumption == "Two misters and two signals are hypothetical simulation infrastructure.":
            continue
        print(f"Simulation assumption: {assumption}")
    if misting_scenario is not None:
        print(f"Misting scenario: {arguments.misting_count} hypothetical misters, "
              f"{misting_scenario['mist_range_m']:g} m radius; other actuators preserved")
    final_output = run_simulation(
        graph=graph,
        rounds=arguments.rounds,
        debug_directory=arguments.debug_dir,
        context={
            "time": arguments.time,
            "scenario": arguments.scenario,
            "dataset_dir": str(arguments.dataset),
            "pedestrian_dt_sec": arguments.pedestrian_dt,
            **({"misting_scenario": misting_scenario} if misting_scenario is not None else {}),
        },
        pedestrians=pedestrians,
        trip_requests=requests,
        dataset_directory=arguments.dataset,
    )

    print(f"Pipeline completed after {arguments.rounds} round(s): {final_output.model_name}")
    if final_output.property_names:
        property_names = ", ".join(
            f"'{name}'" for name in final_output.property_names
        )
        target_count = len(
            final_output.property_values[final_output.property_names[0]]
        )
        print(
            f"Generated {property_names} for "
            f"{target_count} {final_output.property_target}s."
        )
    else:
        print("Updated graph state without generating new properties.")
    state = final_output.diagnostics["pedestrian_state"]
    summary = state["summary"]
    print(
        f"Pedestrians: {summary['total_pedestrians']} total, "
        f"{summary['active_pedestrians']} active, "
        f"{summary['completed_trips']} arrived; "
        f"elapsed {state['simulation_time_sec']:g} s"
    )
    total_wait = sum(p["total_wait_time_sec"] for p in state["pedestrians"])
    print(f"Total pedestrian signal waiting: {total_wait:g} person-seconds")
    verification = final_output.diagnostics["verification"]
    print(f"Verification audit: {verification['status']}; "
          f"simulation overall {verification['simulation_status']}; "
          f"route selection {verification['route_selection_status']}; "
          f"bounded arrival {verification['bounded_arrival_status']}; "
          f"actuators {verification['actuator_response']['status']}")
    print("Final actuator states:")
    for actuator in final_output.graph.actuators.values():
        details = f"status={actuator.properties.get('status')}"
        if actuator.actuator_type == "traffic_signal":
            details += f", wait_time_s={actuator.properties.get('wait_time_s')}"
        elif actuator.actuator_type == "directional_led":
            details += f", recommended_edge={actuator.properties.get('recommended_edge')}"
        print(f"  {actuator.actuator_id} ({actuator.actuator_type}): {details}")
    if arguments.debug_dir is not None:
        print(f"Debug outputs saved to: {arguments.debug_dir}")


if __name__ == "__main__":
    main()

