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


def get_edge_distance(
    graph: CampusGraph,
    from_node: str,
    to_node: str
) -> float:
    """
    Return the distance of the walkway connecting two nodes.

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

            return edge.distance_m

    raise ValueError(
        f"No edge exists between {from_node} and {to_node}"
    )


def move_pedestrian(
    pedestrian: Pedestrian,
    graph: CampusGraph,
    dt: float
) -> None:
    """
    Advance a pedestrian along their assigned route.

    The pedestrian's position, route index, movement state,
    and accumulated travel time are updated in place.

    Args:
        pedestrian: Pedestrian agent being updated.
        graph: Campus graph containing walkway information.
        dt: Simulation timestep in seconds.
    """

    # Validate the simulation timestep.
    if not math.isfinite(dt) or dt < 0:
        raise ValueError(
            "Timestep must be finite and nonnegative"
        )

    # Validate walking speed.
    if (
        not math.isfinite(pedestrian.walking_speed_mps)
        or pedestrian.walking_speed_mps <= 0
    ):
        raise ValueError(
            "Walking speed must be finite and positive"
        )

    # Only walking pedestrians can move.
    if pedestrian.state != PedestrianState.WALKING:
        return

    # Validate the route and current position before changing
    # pedestrian state. This prevents partial movement when
    # an invalid route is encountered.
    if (
        len(pedestrian.route) < 2
        or pedestrian.route[0] != pedestrian.origin
        or pedestrian.route[-1] != pedestrian.destination
    ):
        raise ValueError("Pedestrian has an invalid route")

    if not (
        0 <= pedestrian.current_edge_index
        < len(pedestrian.route) - 1
    ):
        raise ValueError("Invalid current edge index")

    edge_lengths = [
        get_edge_distance(graph, start, end)
        for start, end in zip(
            pedestrian.route[:-1],
            pedestrian.route[1:]
        )
    ]

    current_edge_length = edge_lengths[
        pedestrian.current_edge_index
    ]

    if (
        not math.isfinite(pedestrian.position_m)
        or pedestrian.position_m < 0
        or pedestrian.position_m > current_edge_length
    ):
        raise ValueError("Invalid pedestrian position")

    # Calculate how far the pedestrian can travel.
    remaining_distance = pedestrian.walking_speed_mps * dt

    if not math.isfinite(remaining_distance):
        raise ValueError("Movement distance must be finite")

    distance_traveled = 0.0

    # Continue moving until the timestep's available
    # movement distance is exhausted or the pedestrian arrives.
    while remaining_distance > 0:

        edge_distance = edge_lengths[
            pedestrian.current_edge_index
        ]

        distance_to_edge_end = (
            edge_distance - pedestrian.position_m
        )

        if remaining_distance < distance_to_edge_end:

            # The pedestrian remains on the current edge.
            pedestrian.position_m += remaining_distance

            distance_traveled += remaining_distance
            remaining_distance = 0.0

        else:

            # The pedestrian reaches the end of the edge.
            remaining_distance -= distance_to_edge_end
            distance_traveled += distance_to_edge_end

            pedestrian.current_edge_index += 1
            pedestrian.position_m = 0.0

            # Check whether the pedestrian reached
            # their final destination.
            if (
                pedestrian.current_edge_index
                >= len(pedestrian.route) - 1
            ):
                pedestrian.state = PedestrianState.ARRIVED
                break

    # Record actual walking time consumed.
    # This avoids counting unused timestep time after arrival.
    pedestrian.total_travel_time_sec += (
        distance_traveled / pedestrian.walking_speed_mps
    )