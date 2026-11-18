"""Standalone USC movement demo with fixed routes and dataset signal commands."""
from dataclasses import replace
import math
from pathlib import Path

from utils import ModelInput, ModelOutput, load_campus_graph
from .model import PedestrianSimulator
from .movement import get_edge_distance


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"
DEFAULT_ROUTES_PATH = Path(__file__).with_name("example_usc_routes.json")


def run_standalone(
    dataset: str | Path = DEFAULT_DATASET_PATH,
    routes: str | Path = DEFAULT_ROUTES_PATH,
    steps: int = 10,
    dt: float = 10.0,
) -> ModelOutput:
    """Advance fixed routes on one persistent simulator, retaining every snapshot."""
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError("Steps must be a positive integer")
    if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt < 0:
        raise ValueError("Timestep must be finite and nonnegative")
    graph = load_campus_graph(dataset)
    simulator = PedestrianSimulator.from_routes_file(graph, routes)
    lengths = {
        pid: [get_edge_distance(graph, a, b) for a, b in zip(agent.route, agent.route[1:])]
        for pid, agent in simulator.pedestrians.items()
    }

    def snapshot():
        state = simulator.get_state()
        for person in state["pedestrians"]:
            segments = lengths[person["pedestrian_id"]]
            person["route_length_m"] = sum(segments)
            person["distance_walked_m"] = sum(segments[:person["current_edge_index"]]) + person["position_m"]
        return state

    output = simulator.predict(ModelInput(graph, context={"pedestrian_dt_sec": 0}))
    snapshots = [snapshot()]
    for _ in range(steps):
        output = simulator.predict(ModelInput(output.graph, context={"pedestrian_dt_sec": dt}))
        snapshots.append(snapshot())
    return replace(output, diagnostics={
        **output.diagnostics,
        "dataset_dir": str(dataset), "routes_file": str(routes),
        "steps": steps, "dt_sec": dt,
        "signal_commands": {
            key: {"status": actuator.properties.get("status"),
                  "wait_time_s": actuator.properties.get("wait_time_s")}
            for key, actuator in graph.actuators.items() if actuator.actuator_type == "traffic_signal"
        },
        "snapshots": snapshots,
    })


def main(argv=None) -> ModelOutput:
    import argparse

    parser = argparse.ArgumentParser(description="Move pedestrians on fixed USC routes, including a signal crossing.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES_PATH,
                        help="Fixed node routes matching the selected dataset")
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--dt", type=float, default=10.0, help="Simulated seconds per step (default: 10)")
    parser.add_argument("--save", type=Path, help="Save final occupancy and all movement snapshots as JSON")
    arguments = parser.parse_args(argv)
    output = run_standalone(arguments.dataset, arguments.routes, arguments.steps, arguments.dt)
    print(f"Dataset: {arguments.dataset}; fixed routes: {arguments.routes}")
    print(f"Dataset signal commands: {output.diagnostics['signal_commands']}")
    for state in output.diagnostics["snapshots"]:
        print(f"Time {state['simulation_time_sec']:g} s")
        for person in state["pedestrians"]:
            print(f"  P{person['pedestrian_id']}: {person['state']}; "
                  f"{person['distance_walked_m']:.2f}/{person['route_length_m']:.2f} m; "
                  f"segment {min(person['current_edge_index'] + 1, len(person['route']) - 1)}"
                  f"/{len(person['route']) - 1}, position {person['position_m']:.2f} m; "
                  f"walking {person['total_travel_time_sec']:.2f} s, "
                  f"waiting {person['total_wait_time_sec']:.2f} s, "
                  f"wait remaining {person['signal_wait_remaining_sec']:.2f} s")
    if arguments.save:
        print(f"Saved to {output.save_json(arguments.save)}")
    return output


if __name__ == "__main__":
    main()
