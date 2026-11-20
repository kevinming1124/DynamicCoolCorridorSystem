"""
Pedestrian Movement Model

Implements movement logic for pedestrian agents traveling through
the campus graph.

Updates pedestrian positions based on walking speed and simulation
time, handles transitions between connected walkways, and detects
when pedestrians reach their destinations.

Pedestrian attributes and states are defined in agent.py.
"""

import math

from utils.graph.graph import CampusGraph
from utils.graph.entities import GraphEdge

from .agent import Pedestrian, PedestrianState


# TODO: Discuss support for multiple edges between the same two nodes.
#
# Current assumptions:
# 1. Each connected node pair has exactly one walkway.
# 2. Walkways are bidirectional.
#
# Limitation:
# If multiple walkways connect the same nodes, identifying an edge
# using only its endpoints becomes ambiguous.
#
# Potential improvement:
# Represent pedestrian routes using explicit edge IDs.


def get_route_edge(
    graph: CampusGraph,
    from_node: str,
    to_node: str
) -> GraphEdge:
    """
    Return the edge of the walkway connecting two nodes.

    Raises ValueError if the walkway does not exist, is inaccessible,
    or has an invalid distance.
    """

    for edge in graph.edges:

        forward = (
            edge.from_node == from_node
            and edge.to_node == to_node
        )

        backward = (
            edge.from_node == to_node
            and edge.to_node == from_node
        )

        if forward or backward:

            # TODO: Confirm the exact meaning of accessibility
            # with the team.
            #
            # Current assumption:
            # accessible=False means pedestrians cannot traverse
            # this walkway.

            if not edge.accessible:
                raise ValueError(
                    f"Edge between {from_node} and {to_node} "
                    "is inaccessible"
                )

            if (
                not math.isfinite(edge.distance_m)
                or edge.distance_m <= 0
            ):
                raise ValueError(
                    f"Invalid distance between "
                    f"{from_node} and {to_node}"
                )

            return edge

    raise ValueError(
        f"No edge exists between {from_node} and {to_node}"
    )


def get_edge_distance(graph: CampusGraph, from_node: str, to_node: str) -> float:
    return get_route_edge(graph, from_node, to_node).distance_m


def validate_signal_wait(pedestrian: Pedestrian) -> None:
    """Validate countdown state before movement or simulator registration."""
    remaining = pedestrian.signal_wait_remaining_sec
    index = pedestrian.signal_wait_edge_index
    ids = pedestrian.waiting_signal_ids
    if (isinstance(remaining, bool) or not isinstance(remaining, (int, float))
            or not math.isfinite(remaining) or remaining < 0):
        raise ValueError("Signal wait remaining must be finite and nonnegative")
    if index is not None and (isinstance(index, bool) or not isinstance(index, int)
                              or not 0 <= index < len(pedestrian.route) - 1):
        raise ValueError("Invalid signal wait edge index")
    if (not isinstance(ids, tuple) or any(not isinstance(s, str) or not s for s in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("Waiting signal IDs must be a tuple of unique strings")
    if remaining > 0:
        if (pedestrian.state != PedestrianState.WAITING or pedestrian.position_m != 0
                or index != pedestrian.current_edge_index or not ids):
            raise ValueError("Signal waiting requires a stopped pedestrian at the current departure")
    elif ids:
        raise ValueError("Released pedestrians cannot retain active waiting signal IDs")


def signal_delay(graph: CampusGraph, node: str, edge_id: str) -> tuple[float, tuple[str, ...]]:
    """Sample the latest wait before leaving an anchor on a controlled edge.

    Co-located controls run concurrently, so apply their maximum delay once.
    """
    waits = []
    for signal in graph.actuators.values():
        if (signal.actuator_type != "traffic_signal" or signal.anchor_node != node
                or edge_id not in signal.controlled_edges):
            continue
        wait = signal.properties.get("wait_time_s")
        if (isinstance(wait, bool) or not isinstance(wait, (int, float))
                or not math.isfinite(wait) or wait < 0):
            raise ValueError(f"{signal.actuator_id}: wait_time_s must be finite and nonnegative")
        if ((signal.min_wait_time_s is not None and wait < signal.min_wait_time_s)
                or (signal.max_wait_time_s is not None and wait > signal.max_wait_time_s)):
            raise ValueError(f"{signal.actuator_id}: wait_time_s exceeds signal limits")
        waits.append((signal.actuator_id, float(wait)))
    return max((wait for _, wait in waits), default=0.0), tuple(sorted(s for s, _ in waits))


def move_pedestrian(pedestrian: Pedestrian, graph: CampusGraph, dt: float) -> None:
    """Spend dt on signal waiting and walking, stopping at arrival.

    Wait once per controlled departure, including a signal at the origin. No
    wait is needed after arriving at the destination. Capture the delay when
    reached; subsequent controller changes do not restart an active countdown.
    Generic WAITING agents retain their previous behavior. Travel time remains
    walking time; total_wait_time_sec records actual signal delay consumed.
    """
    if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt < 0:
        raise ValueError("Timestep must be finite and nonnegative")
    if (not math.isfinite(pedestrian.walking_speed_mps)
            or pedestrian.walking_speed_mps <= 0):
        raise ValueError("Walking speed must be finite and positive")
    validate_signal_wait(pedestrian)
    if pedestrian.state == PedestrianState.ARRIVED:
        return
    if pedestrian.state == PedestrianState.WAITING and pedestrian.signal_wait_remaining_sec == 0:
        return
    if (len(pedestrian.route) < 2 or pedestrian.route[0] != pedestrian.origin
            or pedestrian.route[-1] != pedestrian.destination):
        raise ValueError("Pedestrian has an invalid route")
    if not 0 <= pedestrian.current_edge_index < len(pedestrian.route) - 1:
        raise ValueError("Invalid current edge index")
    edges = [get_route_edge(graph, a, b) for a, b in zip(pedestrian.route, pedestrian.route[1:])]
    current_length = edges[pedestrian.current_edge_index].distance_m
    if not math.isfinite(pedestrian.position_m) or not 0 <= pedestrian.position_m <= current_length:
        raise ValueError("Invalid pedestrian position")
    if not math.isfinite(pedestrian.walking_speed_mps * dt):
        raise ValueError("Movement distance must be finite")
    # Validate settings before changing an agent, even if a later crossing is bad.
    delays = [signal_delay(graph, node, edge.edge_id)
              for node, edge in zip(pedestrian.route, edges)]
    remaining = dt
    while remaining > 0:
        index = pedestrian.current_edge_index
        if pedestrian.position_m == 0 and pedestrian.signal_wait_edge_index != index:
            delay, signals = delays[index]
            pedestrian.signal_wait_edge_index = index
            if delay > 0:
                pedestrian.signal_wait_remaining_sec = delay
                pedestrian.waiting_signal_ids = signals
                pedestrian.state = PedestrianState.WAITING
        if pedestrian.signal_wait_remaining_sec > 0:
            waited = min(remaining, pedestrian.signal_wait_remaining_sec)
            pedestrian.signal_wait_remaining_sec -= waited
            pedestrian.total_wait_time_sec += waited
            remaining -= waited
            if pedestrian.signal_wait_remaining_sec > 0:
                break
            pedestrian.waiting_signal_ids = ()
            pedestrian.state = PedestrianState.WALKING
            if remaining == 0:
                break
        distance_left = edges[index].distance_m - pedestrian.position_m
        walking_time = distance_left / pedestrian.walking_speed_mps
        if remaining < walking_time:
            pedestrian.position_m += pedestrian.walking_speed_mps * remaining
            pedestrian.total_travel_time_sec += remaining
            break
        remaining -= walking_time
        pedestrian.total_travel_time_sec += walking_time
        pedestrian.current_edge_index += 1
        pedestrian.position_m = 0.0
        if pedestrian.current_edge_index == len(pedestrian.route) - 1:
            pedestrian.state = PedestrianState.ARRIVED
            break
