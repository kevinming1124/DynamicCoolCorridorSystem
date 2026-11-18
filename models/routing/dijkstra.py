"""Hand-written Dijkstra search for walking routes on the campus graph.

This file knows nothing about heat. It only knows how to find the cheapest
path when someone else tells it what each step costs. Keeping the search and
the cost formula apart means we can change the formula (costs.py) without
touching the search, and vice versa.

Run the self-check from the repository root:

    python -m models.routing.dijkstra
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Callable, Iterable

from utils import CampusGraph, GraphEdge


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteStep:
    """One edge of a route, in the direction the person walks it."""

    from_node: str
    to_node: str
    edge_id: str


@dataclass(frozen=True)
class RouteResult:
    """A finished route: the steps to walk and what the search thought it cost."""

    steps: tuple[RouteStep, ...]
    total_cost: float       # the number Dijkstra minimized (mixes time and heat)
    total_minutes: float    # walking time (plus waits, once signals are added)

    @property
    def nodes(self) -> list[str]:
        """Every node visited, start to finish (handy for printing and drawing)."""
        if not self.steps:
            return []
        return [self.steps[0].from_node] + [step.to_node for step in self.steps]


# A step-cost function answers: "walking `edge` from `from_node` to `to_node`,
# having already walked `elapsed_min` minutes, what does this step cost, and
# how many minutes does it take?" It returns (cost, minutes).
#
# Why pass elapsed_min? A heat forecast (added later) needs to know WHEN the
# walker reaches the edge, not just which edge it is.
StepCost = Callable[[GraphEdge, str, str, float], "tuple[float, float]"]


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------
def build_adjacency(graph: CampusGraph) -> dict[str, list[tuple[str, GraphEdge]]]:
    """For every node, list (neighbor, edge) pairs.

    Every edge in this project is bidirectional, so each edge is added twice:
    once from each end. The dataset stores it only once (from_node -> to_node).
    """
    adjacency: dict[str, list[tuple[str, GraphEdge]]] = {
        node_id: [] for node_id in graph.nodes
    }
    for edge in graph.edges:
        adjacency[edge.from_node].append((edge.to_node, edge))
        adjacency[edge.to_node].append((edge.from_node, edge))
    return adjacency


def resolve_nodes(graph: CampusGraph, name_or_id: str) -> set[str]:
    """Turn a place name ("Leavey Library") or a node ID ("N37") into node IDs.

    Returns a SET because a building can have several entrances. In campus_50
    every place is a single node, but a real-campus dataset will have more.
    """
    if name_or_id in graph.nodes:
        return {name_or_id}
    matches = {
        node.node_id
        for node in graph.nodes.values()
        if node.location_name == name_or_id
    }
    if not matches:
        known = sorted(n.location_name for n in graph.nodes.values() if n.location_name)
        raise ValueError(
            f"Unknown place or node '{name_or_id}'. Known places: {', '.join(known)}"
        )
    return matches


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------
def dijkstra(
    adjacency: dict[str, list[tuple[str, GraphEdge]]],
    start: str,
    targets: Iterable[str],
    step_cost: StepCost,
    blocked: frozenset[str] = frozenset(),
) -> RouteResult | None:
    """Cheapest route from `start` to whichever node in `targets` is reached first.

    Returns None when no target can be reached (for example, every path is blocked).
    """
    targets = set(targets)
    if start in targets:  # already there: an empty route
        return RouteResult(steps=(), total_cost=0.0, total_minutes=0.0)

    best_cost = {start: 0.0}     # cheapest cost found so far to reach each node
    minutes_at = {start: 0.0}    # walking time along that cheapest path
    came_from: dict[str, RouteStep] = {}   # the last step of that cheapest path
    finished: set[str] = set()   # nodes whose cheapest cost is final

    # The heap always hands back the entry with the smallest cost.
    # Entries are (cost so far, node).
    heap: list[tuple[float, str]] = [(0.0, start)]

    while heap:
        cost_here, node = heapq.heappop(heap)

        # "Lazy deletion": Python's heap can't lower an entry's priority, so when
        # we find a cheaper way to a node we just push a NEW entry. The old,
        # more expensive one is still in the heap; when it pops out later, we
        # skip it here.
        if node in finished:
            continue
        finished.add(node)

        # Dijkstra finalizes nodes in order of increasing cost, so the FIRST
        # target we finalize is the cheapest target. This is what makes
        # "go to any entrance of this building" work.
        if node in targets:
            return _rebuild(came_from, start, node, cost_here, minutes_at[node])

        for neighbor, edge in adjacency[node]:
            if edge.edge_id in blocked:   # closed walkway: pretend it isn't there
                continue
            if neighbor in finished:      # already final; can't be improved
                continue
            step, minutes = step_cost(edge, node, neighbor, minutes_at[node])
            if step < 0:
                raise ValueError(f"{edge.edge_id}: Dijkstra needs costs >= 0, got {step}")
            new_cost = cost_here + step
            if new_cost < best_cost.get(neighbor, float("inf")):
                best_cost[neighbor] = new_cost
                minutes_at[neighbor] = minutes_at[node] + minutes
                came_from[neighbor] = RouteStep(node, neighbor, edge.edge_id)
                heapq.heappush(heap, (new_cost, neighbor))

    return None  # heap ran dry without reaching any target


def _rebuild(
    came_from: dict[str, RouteStep],
    start: str,
    end: str,
    total_cost: float,
    total_minutes: float,
) -> RouteResult:
    """Walk backwards from the end through `came_from`, then reverse."""
    steps: list[RouteStep] = []
    node = end
    while node != start:
        step = came_from[node]
        steps.append(step)
        node = step.from_node
    steps.reverse()
    return RouteResult(tuple(steps), total_cost, total_minutes)


# ---------------------------------------------------------------------------
# Self-check: run this file directly
# ---------------------------------------------------------------------------
def _check_against_floyd_warshall(graph: CampusGraph, pairs: int = 25, seed: int = 0) -> None:
    """Compare our Dijkstra with a completely different algorithm.

    Floyd-Warshall computes ALL shortest distances with three nested loops. It
    shares no code with Dijkstra, so if both agree on random pairs, we can trust
    the Dijkstra implementation. Cost here is plain distance in meters.
    """
    import random

    nodes = sorted(graph.nodes)
    dist = {a: {b: (0.0 if a == b else float("inf")) for b in nodes} for a in nodes}
    for edge in graph.edges:
        d = edge.distance_m
        dist[edge.from_node][edge.to_node] = min(dist[edge.from_node][edge.to_node], d)
        dist[edge.to_node][edge.from_node] = min(dist[edge.to_node][edge.from_node], d)
    for k in nodes:
        for i in nodes:
            for j in nodes:
                if dist[i][k] + dist[k][j] < dist[i][j]:
                    dist[i][j] = dist[i][k] + dist[k][j]

    adjacency = build_adjacency(graph)

    def by_distance(edge, from_node, to_node, elapsed_min):
        return edge.distance_m, edge.walk_time_min

    rng = random.Random(seed)
    for _ in range(pairs):
        a, b = rng.sample(nodes, 2)
        route = dijkstra(adjacency, a, {b}, by_distance)
        assert route is not None, f"no route {a} -> {b}"
        assert abs(route.total_cost - dist[a][b]) < 1e-6, (
            f"{a} -> {b}: Dijkstra {route.total_cost} vs Floyd-Warshall {dist[a][b]}"
        )
        # The steps must actually connect start to end.
        assert route.nodes[0] == a and route.nodes[-1] == b
    print(f"Dijkstra matches Floyd-Warshall on {pairs} random pairs.")


if __name__ == "__main__":
    from pathlib import Path

    from utils import load_campus_graph

    from .costs import PlaceholderHeat, make_step_cost

    graph = load_campus_graph(Path(__file__).parents[2] / "dataset" / "campus_50")
    _check_against_floyd_warshall(graph)

    heat = PlaceholderHeat(graph, time_text="13:00")
    adjacency = build_adjacency(graph)
    start, destination = "Leavey Library", "Seeley G. Mudd Building"
    print(f"\n{start} -> {destination} (placeholder heat at 13:00)\n")
    print(f"{'preference':<12}{'minutes':>9}{'cost':>9}   route")
    for preference in (0.0, 0.5, 1.0):
        route = dijkstra(
            adjacency,
            next(iter(resolve_nodes(graph, start))),
            resolve_nodes(graph, destination),
            make_step_cost(heat, preference),
        )
        assert route is not None
        print(f"{preference:<12}{route.total_minutes:>9.2f}{route.total_cost:>9.2f}   "
              + " > ".join(route.nodes))
