"""Run the Dynamic Cool Corridor System model pipeline."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from models.control import ControlModel
from models.environment import EnvironmentModel
from models.heat_exposure import HeatExposureModel
from models.pedestrian.model import PedestrianSimulator
from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph


PROJECT_ROOT = Path(__file__).parent
DEFAULT_DATASET = PROJECT_ROOT / "dataset" / "campus_50"


class PipelineStage(Protocol):
    """Minimum interface required for a pipeline model."""

    def predict(self, model_input: ModelInput) -> ModelOutput:
        ...


def build_initialization_pipeline() -> list[PipelineStage]:
    """Calculate the environmental and heat inputs needed by control."""
    return [
        EnvironmentModel.from_config(),
        HeatExposureModel.from_config(),
    ]


def build_pipeline(pedestrians: PedestrianSimulator) -> list[PipelineStage]:
    """Create one round, reusing the supplied pedestrian simulator."""
    return [
        ControlModel.from_config(),
        *build_initialization_pipeline(),
        pedestrians,
    ]


def run_pipeline(
    graph: CampusGraph,
    stages: Sequence[PipelineStage],
    debug_directory: str | Path | None = None,
    context: Mapping[str, Any] | None = None,
) -> ModelOutput:
    """Run every stage and return the final output."""
    if not stages:
        raise ValueError("The pipeline must contain at least one model stage")

    runtime_context = context or {}
    model_input = ModelInput(graph=graph, context=runtime_context)
    final_output: ModelOutput | None = None

    for stage_number, stage in enumerate(stages, start=1):
        final_output = stage.predict(model_input)
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
) -> ModelOutput:
    """Initialize once, then carry the graph through exactly ``rounds`` rounds.

    Time and scenario remain fixed. The final heat values reflect the final
    round's actuator commands; control is not rerun after those calculations.
    """
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("Rounds must be a positive integer")
    debug_root = Path(debug_directory) if debug_directory is not None else None
    print("Initializing: Environment -> Heat Exposure")
    output = run_pipeline(
        graph=graph,
        stages=build_initialization_pipeline(),
        debug_directory=debug_root / "initialization" if debug_root is not None else None,
        context=context,
    )
    # Build the stage list once; never recreate agents inside the round loop.
    simulator = pedestrians if pedestrians is not None else PedestrianSimulator(output.graph)
    stages = build_pipeline(simulator)
    for round_number in range(1, rounds + 1):
        print(
            f"Round {round_number}/{rounds}: "
            "Control -> Environment -> Heat Exposure -> Pedestrian"
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Directory containing dataset.json and its referenced JSON files",
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
        help="Number of control/environment/heat/pedestrian rounds after initialization (default: 1)",
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
    parser.add_argument(
        "--routes",
        type=Path,
        help="JSON file containing initial pedestrian IDs and assigned node routes",
    )
    parser.add_argument(
        "--pedestrian-dt",
        type=float,
        default=1.0,
        help="Movement seconds per round (default: 1.0; zero reports occupancy only)",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    graph = load_campus_graph(arguments.dataset)
    pedestrians = (
        PedestrianSimulator.from_routes_file(graph, arguments.routes)
        if arguments.routes is not None else PedestrianSimulator(graph)
    )
    final_output = run_simulation(
        graph=graph,
        rounds=arguments.rounds,
        debug_directory=arguments.debug_dir,
        context={
            "time": arguments.time,
            "scenario": arguments.scenario,
            "pedestrian_dt_sec": arguments.pedestrian_dt,
        },
        pedestrians=pedestrians,
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

