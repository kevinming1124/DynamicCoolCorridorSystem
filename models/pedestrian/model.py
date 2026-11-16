"""
Pedestrian Simulation Coordinator

Manages pedestrian agents and advances their movement through the
campus graph over simulated time.

This module coordinates pedestrian movement but does not implement
movement calculations, demand generation, or crowd-density modeling.

Pedestrian movement is delegated to movement.py.

The simulator retains its agents between calls. Pipeline calls return a new
CampusGraph containing pedestrian counts without mutating the input graph.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from utils import CampusGraph, ModelInput, ModelOutput

from .agent import Pedestrian, PedestrianState
from .movement import get_edge_distance, move_pedestrian


class PedestrianSimulator:
    """
    Coordinates pedestrian agents and simulation time.

    Each simulation step updates all pedestrians atomically:
    either every update succeeds or no changes are committed.
    """

    def __init__(self, graph: CampusGraph):
        self.graph = graph
        self.pedestrians: dict[int, Pedestrian] = {}
        self.simulation_time_sec = 0.0

    def assign_route(
        self,
        pedestrian_id: int,
        route: Sequence[str],
        *,
        walking_speed_mps: float | None = None,
    ) -> None:
        """Register an externally calculated route, or replace it before departure.

        A new agent's origin and destination are the route endpoints. Existing
        agents retain their endpoints, state, speed (unless supplied), and time
        totals. Replacing a route after movement begins is not supported yet.
        Invalid assignments leave all simulator state unchanged.
        """
        if isinstance(pedestrian_id, bool) or not isinstance(pedestrian_id, int):
            raise ValueError("Pedestrian ID must be an integer")
        if isinstance(route, (str, bytes)) or not isinstance(route, Sequence):
            raise ValueError("Route must be an ordered sequence of node IDs")
        nodes = list(route)
        if len(nodes) < 2 or any(
            not isinstance(node, str) or node not in self.graph.nodes
            for node in nodes
        ):
            raise ValueError("Route must contain at least two existing node IDs")
        if walking_speed_mps is not None and (
            isinstance(walking_speed_mps, bool)
            or not isinstance(walking_speed_mps, (int, float))
        ):
            raise ValueError("Walking speed must be numeric")

        existing = self.pedestrians.get(pedestrian_id)
        if existing is None:
            candidate = Pedestrian(
                pedestrian_id=pedestrian_id,
                origin=nodes[0],
                destination=nodes[-1],
                route=nodes,
                walking_speed_mps=(
                    1.4 if walking_speed_mps is None else walking_speed_mps
                ),
            )
        else:
            if (
                existing.state == PedestrianState.ARRIVED
                or existing.current_edge_index != 0
                or existing.position_m != 0.0
                or existing.total_travel_time_sec != 0.0
            ):
                raise ValueError("Route replacement is only supported before departure")
            if nodes[0] != existing.origin or nodes[-1] != existing.destination:
                raise ValueError("Replacement route must preserve origin and destination")
            candidate = replace(
                existing,
                route=nodes,
                walking_speed_mps=(
                    existing.walking_speed_mps
                    if walking_speed_mps is None else walking_speed_mps
                ),
            )

        # Reuse registration validation without replacing the current agent
        # until the complete assignment has succeeded.
        validator = PedestrianSimulator(self.graph)
        validator.add_pedestrian(candidate)
        self.pedestrians[pedestrian_id] = validator.get_pedestrian(pedestrian_id)

    @classmethod
    def from_routes_file(
        cls, graph: CampusGraph, path: str | Path
    ) -> "PedestrianSimulator":
        """Load initial assignments; this file supplies routes, not an optimizer."""
        route_path = Path(path)
        try:
            raw = json.loads(route_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read pedestrian routes from {route_path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("pedestrians"), list):
            raise ValueError("Route file must contain a 'pedestrians' array")

        simulator = cls(graph)
        for assignment in raw["pedestrians"]:
            if not isinstance(assignment, dict) or not {
                "pedestrian_id", "route"
            }.issubset(assignment):
                raise ValueError("Each assignment requires 'pedestrian_id' and 'route'")
            pedestrian_id = assignment["pedestrian_id"]
            if isinstance(pedestrian_id, bool) or not isinstance(pedestrian_id, int):
                raise ValueError("Pedestrian ID must be an integer")
            if pedestrian_id in simulator.pedestrians:
                raise ValueError(f"Duplicate pedestrian ID in route file: {pedestrian_id}")
            simulator.assign_route(
                pedestrian_id,
                assignment["route"],
                walking_speed_mps=assignment.get("walking_speed_mps"),
            )
        return simulator

    def add_pedestrian(self, pedestrian: Pedestrian) -> None:
        """
        Register a pedestrian after validating their route.

        Raises:
            ValueError: If the ID is duplicated or the route is invalid.
        """

        if pedestrian.pedestrian_id in self.pedestrians:
            raise ValueError(
                f"Pedestrian ID {pedestrian.pedestrian_id} already exists"
            )

        if (
            len(pedestrian.route) < 2
            or pedestrian.route[0] != pedestrian.origin
            or pedestrian.route[-1] != pedestrian.destination
        ):
            raise ValueError("Pedestrian has an invalid route")

        # Verify that every route segment exists and is accessible.
        edge_lengths = [
            get_edge_distance(self.graph, start, end)
            for start, end in zip(
                pedestrian.route[:-1],
                pedestrian.route[1:]
            )
        ]

        if (
            not math.isfinite(pedestrian.walking_speed_mps)
            or pedestrian.walking_speed_mps <= 0
        ):
            raise ValueError("Walking speed must be finite and positive")

        if not (
            0 <= pedestrian.current_edge_index
            <= len(pedestrian.route) - 1
        ):
            raise ValueError("Invalid current edge index")

        if pedestrian.state == PedestrianState.ARRIVED:
            if (
                pedestrian.current_edge_index
                != len(pedestrian.route) - 1
                or pedestrian.position_m != 0.0
            ):
                raise ValueError("Invalid arrived pedestrian state")
        else:
            if pedestrian.current_edge_index >= len(edge_lengths):
                raise ValueError("Active pedestrian has no remaining edge")

            edge_length = edge_lengths[pedestrian.current_edge_index]

            if (
                not math.isfinite(pedestrian.position_m)
                or not 0 <= pedestrian.position_m <= edge_length
            ):
                raise ValueError("Invalid pedestrian position")

        if (
            not math.isfinite(pedestrian.total_travel_time_sec)
            or pedestrian.total_travel_time_sec < 0
            or not math.isfinite(pedestrian.total_wait_time_sec)
            or pedestrian.total_wait_time_sec < 0
        ):
            raise ValueError("Invalid pedestrian time values")

        # Store a copy so external modifications to the original
        # pedestrian cannot change the simulator's internal state.
        self.pedestrians[pedestrian.pedestrian_id] = replace(
            pedestrian,
            route=list(pedestrian.route),
        )

    def step(self, dt: float = 1.0) -> None:
        """
        Advance the entire pedestrian simulation by dt seconds.

        Updates are calculated on copies of pedestrian states.
        Changes are committed only if every movement succeeds.
        """

        if isinstance(dt, bool) or not isinstance(dt, (int, float)):
            raise ValueError("Timestep must be numeric")

        if not math.isfinite(dt) or dt < 0:
            raise ValueError("Timestep must be finite and nonnegative")

        new_time = self.simulation_time_sec + dt

        if not math.isfinite(new_time):
            raise ValueError("Simulation time must remain finite")

        # Create independent copies of pedestrian states.
        updated_pedestrians = {
            pedestrian_id: replace(
                pedestrian,
                route=list(pedestrian.route),
            )
            for pedestrian_id, pedestrian in self.pedestrians.items()
        }

        # Update the copies, not the original pedestrians.
        for pedestrian in updated_pedestrians.values():
            move_pedestrian(
                pedestrian=pedestrian,
                graph=self.graph,
                dt=dt,
            )

        # Commit only after every movement succeeds.
        self.pedestrians = updated_pedestrians
        self.simulation_time_sec = new_time

    def predict(self, model_input: ModelInput) -> ModelOutput:
        """Advance one step on the latest graph and report edge occupancy.

        ``context["pedestrian_dt_sec"]`` overrides the one-second default.
        Counts describe the end of the step, include waiting pedestrians on
        their assigned edge, and exclude arrived pedestrians. All state is
        committed only after movement and output validation succeed.
        """
        candidate = PedestrianSimulator(model_input.graph)
        candidate.simulation_time_sec = self.simulation_time_sec
        for pedestrian in self.pedestrians.values():
            candidate.add_pedestrian(pedestrian)
        candidate.step(model_input.context.get("pedestrian_dt_sec", 1.0))

        counts = {edge.edge_id: 0 for edge in candidate.graph.edges}
        edges_by_nodes = {}
        for edge in candidate.graph.edges:
            pair = frozenset((edge.from_node, edge.to_node))
            edges_by_nodes.setdefault(pair, []).append(edge.edge_id)

        for pedestrian in candidate.pedestrians.values():
            if pedestrian.state == PedestrianState.ARRIVED:
                continue
            index = pedestrian.current_edge_index
            pair = frozenset(pedestrian.route[index:index + 2])
            edge_ids = edges_by_nodes.get(pair, [])
            if len(edge_ids) != 1:
                raise ValueError(
                    "Pedestrian occupancy requires exactly one edge per "
                    f"active route segment: {pedestrian.route[index:index + 2]}"
                )
            counts[edge_ids[0]] += 1

        graph = candidate.graph.with_edge_property("pedestrian_count", counts)
        output = ModelOutput(
            model_name="pedestrian_simulation",
            property_target="edge",
            property_names=("pedestrian_count",),
            property_values={"pedestrian_count": counts},
            graph=graph,
            diagnostics={"pedestrian_state": candidate.get_state()},
        )
        self.graph = graph
        self.pedestrians = candidate.pedestrians
        self.simulation_time_sec = candidate.simulation_time_sec
        return output

    def get_pedestrian(self, pedestrian_id: int) -> Pedestrian:
        """
        Return a copy of the requested pedestrian.

        Raises KeyError if the pedestrian does not exist.
        """

        pedestrian = self.pedestrians[pedestrian_id]

        return replace(
            pedestrian,
            route=list(pedestrian.route),
        )

    def get_state(self) -> dict:
        """
        Return a snapshot of the current simulation state.

        The returned dictionary contains simulation time,
        pedestrian states, and summary statistics.
        """

        pedestrian_states = []

        for pedestrian in self.pedestrians.values():
            pedestrian_states.append({
                "pedestrian_id": pedestrian.pedestrian_id,
                "origin": pedestrian.origin,
                "destination": pedestrian.destination,
                "route": list(pedestrian.route),
                "current_edge_index": pedestrian.current_edge_index,
                "position_m": pedestrian.position_m,
                "walking_speed_mps": pedestrian.walking_speed_mps,
                "state": pedestrian.state.value,
                "total_travel_time_sec": pedestrian.total_travel_time_sec,
                "total_wait_time_sec": pedestrian.total_wait_time_sec,
            })

        completed = sum(
            pedestrian.state == PedestrianState.ARRIVED
            for pedestrian in self.pedestrians.values()
        )

        waiting = sum(
            pedestrian.state == PedestrianState.WAITING
            for pedestrian in self.pedestrians.values()
        )

        return {
            "simulation_time_sec": self.simulation_time_sec,
            "pedestrians": pedestrian_states,
            "summary": {
                "total_pedestrians": len(self.pedestrians),
                "active_pedestrians": (
                    len(self.pedestrians) - completed
                ),
                "waiting_pedestrians": waiting,
                "completed_trips": completed,
            },
        }