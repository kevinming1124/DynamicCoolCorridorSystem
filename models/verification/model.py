"""Final audit of current-condition routes and implemented actuator responses."""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from utils import ModelInput, ModelOutput
from .actuators import audit_actuators
from .checks import CODES, FAIL, INCONCLUSIVE, NA, PASS, RouteNetwork, aggregate, audit_route, number
from .strel_adapter import RouteSafetyMonitor

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "usc_osm_trimmed"
DEFAULT_TRIPS_PATH = PROJECT_ROOT / "models" / "routing" / "example_signal_trips.json"
OUTPUTS = ("verification_route_selection_valid", "verification_bounded_arrival_valid",
           "verification_fallback_valid", "verification_actuator_response_valid",
           "verification_overall_valid")
INPUTS = ("utci_c", "unsafe_heat", "heat_exposure_rate", "air_temp_f", "humidity_pct", "mist_cooling_f")


@dataclass(frozen=True)
class VerificationConfig:
    model_name: str
    enabled: bool
    maximum_arrival_ratio: float
    comparison_tolerance: float
    formula_path: Path
    formula_name: str

    @classmethod
    def from_file(cls, path=DEFAULT_CONFIG_PATH):
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw.get("model_name"), str) or not raw["model_name"].strip():
            raise ValueError("Verification config requires a model_name")
        if not isinstance(raw.get("enabled"), bool):
            raise ValueError("Verification enabled must be Boolean")
        if (raw.get("property_target") != "edge" or tuple(raw.get("output_properties", ())) != OUTPUTS
                or tuple(raw.get("input_properties", ())) != INPUTS):
            raise ValueError("Verification config must declare the current-condition input/output contract")
        parameters = raw["parameters"]
        ratio = number(parameters["maximum_arrival_ratio"], "maximum_arrival_ratio", minimum=1)
        tolerance = number(parameters["comparison_tolerance"], "comparison_tolerance", minimum=0)
        metadata = raw["metadata"]
        for key in ("formula_file", "formula_name"):
            if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                raise ValueError(f"metadata.{key} must be a nonempty string")
        return cls(raw["model_name"].strip(), raw["enabled"], ratio, tolerance,
                   path.parent / metadata["formula_file"], metadata["formula_name"])


def evidence(reports, key):
    matches = [report for report in reports.values() if key in report]
    if len(matches) != 1:
        raise ValueError(f"Verification needs exactly one same-round producer of '{key}'")
    return matches[0]


def validate_snapshot(snapshot, name):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("pedestrians"), list):
        raise ValueError(f"Missing {name} pedestrian snapshot")
    number(snapshot.get("simulation_time_sec"), f"{name}.simulation_time_sec", minimum=0)
    required = {"pedestrian_id", "origin", "destination", "route", "current_edge_index", "position_m",
                "walking_speed_mps", "state", "signal_wait_remaining_sec", "signal_wait_edge_index",
                "waiting_signal_ids", "total_travel_time_sec", "total_wait_time_sec"}
    ids = set()
    for person in snapshot["pedestrians"]:
        if not isinstance(person, dict) or required - person.keys():
            raise ValueError(f"{name}: incomplete pedestrian evidence")
        pid = person["pedestrian_id"]
        if isinstance(pid, bool) or not isinstance(pid, int) or pid in ids:
            raise ValueError(f"{name}: invalid or duplicate pedestrian_id")
        ids.add(pid)
        if person["state"] not in ("walking", "waiting", "arrived"):
            raise ValueError(f"P{pid}: invalid pedestrian state")
        for key in ("total_travel_time_sec", "total_wait_time_sec", "signal_wait_remaining_sec", "position_m"):
            number(person[key], f"P{pid}.{key}", minimum=0)


class VerificationModel:
    """Observe and report; never assign routes, move agents, or change commands."""
    def __init__(self, config, route_monitor):
        self.config = config
        self.route_monitor = route_monitor
        self.round_number = 0
        self.history = []

    @classmethod
    def from_config(cls, path=DEFAULT_CONFIG_PATH):
        config = VerificationConfig.from_file(path)
        return cls(config, RouteSafetyMonitor(config.formula_path, config.formula_name))

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config.enabled:
            raise RuntimeError("Verification model is disabled")
        graph, context = model_input.graph, model_input.context
        reports = context.get("stage_diagnostics")
        if not isinstance(reports, dict):
            raise ValueError("Verification requires explicit same-round stage_diagnostics")
        environment = evidence(reports, "base_air_temp_f")
        heat = evidence(reports, "unsafe_utci_c")
        movement = evidence(reports, "pedestrian_state")
        routing = evidence(reports, "pending_trip_ids")
        before = context.get("pedestrian_before_movement")
        after = movement["pedestrian_state"]
        validate_snapshot(before, "before_movement")
        validate_snapshot(after, "after_movement")
        threshold = number(heat["unsafe_utci_c"], "unsafe_utci_c")
        reference = number(heat["reference_utci_c"], "reference_utci_c")
        tolerance = self.config.comparison_tolerance
        # These are rounded model outputs. The original unsafe flag uses the
        # unrounded UTCI, so values rounding onto the threshold can have either flag.
        for edge in graph.edges:
            for name in INPUTS:
                number(edge.properties.get(name), f"{edge.edge_id}.{name}")
            unsafe = edge.properties["unsafe_heat"]
            utci, rate = edge.properties["utci_c"], edge.properties["heat_exposure_rate"]
            if unsafe not in (0.0, 1.0) or rate < 0:
                raise ValueError(f"{edge.edge_id}: invalid heat flags or rate")
            if abs(utci - threshold) > 0.005 + tolerance and unsafe != float(utci >= threshold):
                raise ValueError(f"{edge.edge_id}: unsafe_heat disagrees with Heat Exposure threshold")
            if not math.isclose(rate, max(0.0, utci - reference), rel_tol=0.0, abs_tol=0.011 + tolerance):
                raise ValueError(f"{edge.edge_id}: exposure rate disagrees with UTCI")
            blocked = edge.properties.get("blocked", 0.0)
            if isinstance(blocked, bool) or blocked not in (0.0, 1.0):
                raise ValueError(f"{edge.edge_id}: blocked must be numeric 0 or 1")
        network = RouteNetwork(graph)
        plans = {p["pedestrian_id"]: p for p in routing["all_assigned_routes"]}
        phases = {}
        for phase, snapshot in (("before_movement", before), ("remaining_routes", after)):
            phases[phase] = [audit_route(
                network, person, self.route_monitor, self.config.maximum_arrival_ratio, tolerance,
                plans.get(person["pedestrian_id"], {}).get("destination_nodes"))
                for person in snapshot["pedestrians"]]
        pending = []
        for request in routing["pending_requests"]:
            path = network.search(request["origin_node"], request["destination_nodes"],
                                  request["walking_speed_mps"], mode="time")
            pending.append({"pedestrian_id": request["pedestrian_id"],
                            "status": FAIL if path is not None else NA,
                            "reason": "reachable_request_not_assigned" if path is not None else "no_available_path"})
        dt = number(context.get("pedestrian_dt_sec", 1.0), "pedestrian_dt_sec", minimum=0)
        actuators = audit_actuators(network, environment, before, after, dt, tolerance)
        records = phases["before_movement"] + phases["remaining_routes"]
        selection_status = aggregate([r["route_selection_status"] for r in records] + [r["status"] for r in pending])
        arrival_status = aggregate(r["bounded_arrival_status"] for r in records)
        fallback_status = aggregate(r["fallback_status"] for r in records)
        status = aggregate((selection_status, arrival_status, actuators["status"]))
        round_number = self.round_number + 1
        # Keep compact history so later arrivals cannot erase earlier violations.
        summary = {"round": round_number, "status": status,
                   "route_selection_status": selection_status, "bounded_arrival_status": arrival_status,
                   "actuator_response_status": actuators["status"],
                   "route_audit_counts": dict(Counter(r["status"] for r in records)),
                   "pedestrian_statuses": [
                       {"phase": phase, **{key: record[key] for key in (
                           "pedestrian_id", "status", "route_selection_status", "bounded_arrival_status")}}
                       for phase, assessments in phases.items() for record in assessments]}
        history = self.history + [summary]
        audit = {**summary, "simulation_status": aggregate(r["status"] for r in history),
                 "fallback_status": fallback_status, "mode": "audit_only",
                 "conditions": "current_round_snapshot", "unsafe_utci_c": threshold,
                 "heat_safety_source": "Heat Exposure unsafe_heat (UTCI >= threshold before rounding)",
                 "maximum_arrival_ratio": self.config.maximum_arrival_ratio,
                 "arrival_bound_affects_route_selection": False,
                 "fallback_heat_ceiling_enforced": False,
                 "waiting_exposure_assumption": "outgoing edge current heat_exposure_rate",
                 "before_movement": phases["before_movement"],
                 "remaining_routes": phases["remaining_routes"], "pending_requests": pending,
                 "already_at_destination_ids": routing["already_at_destination_ids"],
                 "actuator_response": actuators, "round_history": history,
                 "engines": {"selected_route_safety": "moonlight_strel",
                             "safe_route_existence": "safe-subgraph Dijkstra",
                             "fallback": "minimax Dijkstra then additive exposure/time Dijkstra",
                             "arrival": "directional time Dijkstra; same committed prefix and countdown"}}
        statuses = (selection_status, arrival_status, fallback_status, actuators["status"], status)
        values = {name: {edge.edge_id: CODES[result] for edge in graph.edges}
                  for name, result in zip(OUTPUTS, statuses)}
        updated = graph
        for name, mapping in values.items():
            updated = updated.with_edge_property(name, mapping)
        output = ModelOutput(self.config.model_name, "edge", OUTPUTS, values, updated,
                             {"pedestrian_state": after,
                              "signal_wait_consumed_sec": movement["signal_wait_consumed_sec"],
                              "verification": audit})
        self.round_number, self.history = round_number, history
        print(f"Verification: {status}; route selection {selection_status}; arrival {arrival_status}; "
              f"actuators {actuators['status']}")
        return output


def run_demo(
    dataset: str | Path = DEFAULT_DATASET_PATH,
    trips: str | Path = DEFAULT_TRIPS_PATH,
    time: str = "13:00",
    scenario: str = "hot",
    rounds: int = 3,
    pedestrian_dt: float = 30.0,
    misting_count: int | None = 4,
) -> ModelOutput:
    """Audit real upstream stages on USC, including movement across a signal."""
    from main import run_simulation
    from models.environment import EnvironmentModel
    from models.routing import load_trip_requests
    from utils import load_campus_graph
    from utils.graph.misting_scenarios import apply_misting_scenario

    number(pedestrian_dt, "pedestrian_dt", minimum=0)
    dataset = Path(dataset)
    graph = load_campus_graph(dataset)
    context = {"time": time, "scenario": scenario, "pedestrian_dt_sec": pedestrian_dt}
    if misting_count is not None:
        radius = EnvironmentModel.from_config().config.parameters["mist_range_m"]
        graph, layout = apply_misting_scenario(graph, dataset, misting_count, radius)
        context["misting_scenario"] = layout
    result = run_simulation(graph, rounds=rounds, dataset_directory=dataset,
                            trip_requests=load_trip_requests(trips), context=context)
    return replace(result, diagnostics={
        **result.diagnostics,
        "demo": {"dataset_dir": str(dataset), "trips_file": str(trips),
                 "time": time, "scenario": scenario, "rounds": rounds,
                 "pedestrian_dt_sec": pedestrian_dt,
                 "misting_count": sum(a.actuator_type == "misting_point" for a in graph.actuators.values()),
                 "misting_layout": "base" if misting_count is None else misting_count},
    })


def main(argv=None) -> ModelOutput:
    import argparse
    from utils.graph.misting_scenarios import MISTING_COUNTS

    parser = argparse.ArgumentParser(description="Audit the full pipeline on USC with signal-crossing trips.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--trips", type=Path, default=DEFAULT_TRIPS_PATH)
    parser.add_argument("--time", default="13:00")
    parser.add_argument("--scenario", default="hot")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--pedestrian-dt", type=float, default=30.0, help="Simulated seconds per round")
    parser.add_argument("--misting-count", choices=[str(n) for n in MISTING_COUNTS] + ["base"], default="4")
    parser.add_argument("--save", type=Path, help="Save final graph and detailed audit with round history as JSON")
    arguments = parser.parse_args(argv)
    try:
        result = run_demo(arguments.dataset, arguments.trips, arguments.time, arguments.scenario,
                          arguments.rounds, arguments.pedestrian_dt,
                          None if arguments.misting_count == "base" else int(arguments.misting_count))
        audit = result.diagnostics["verification"]
        print(f"Audit completed: dataset {arguments.dataset}; "
              f"misters {result.diagnostics['demo']['misting_count']}; "
              f"{arguments.time}, scenario '{arguments.scenario}'")
        for record in audit["round_history"]:
            print(f"Round {record['round']}: {record['status']}; "
                  f"route selection {record['route_selection_status']}; "
                  f"arrival bound {record['bounded_arrival_status']}; "
                  f"actuators {record['actuator_response_status']}")
        print(f"Simulation audit: {audit['simulation_status']}")
        print(f"Pedestrians: {result.diagnostics['pedestrian_state']['summary']}")
        if arguments.save:
            print(f"Saved to {result.save_json(arguments.save)}")
    except Exception as exc:
        parser.exit(2, f"Verification execution error: {exc}\n")
    if audit["simulation_status"] == FAIL:
        print("Audit detected a requirement violation; execution completed successfully.")
        raise SystemExit(1)
    return result


if __name__ == "__main__":
    main()
