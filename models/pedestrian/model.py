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

import math
from dataclasses import replace

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