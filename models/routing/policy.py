"""Heat-safety route policy used when Routing assigns a trip.

Order of decisions for one trip, using the current round's heat:

1. If any route uses only safe walkways (Heat Exposure ``unsafe_heat`` = 0),
   choose among safe walkways only. The pedestrian's preference still picks
   between safe routes with routingc's distance/comfort cost (0 = shortest,
   1 = most comfortable). If that route takes longer than
   ``maximum_arrival_ratio`` times the fastest available route, use the
   fastest safe route instead; safety still wins if even that is too slow.
2. If no safe route exists, choose the coolest fallback: least cumulative heat
   exposure (exposure rate x walking-plus-waiting minutes), then the lower
   hottest segment, then shorter travel time. It is labelled with how far its
   hottest segment goes past the unsafe line.

Travel time includes signal waits when departing a signal's anchor node on one
of its controlled edges (co-located signals: the longest wait). Blocked or
inaccessible walkways are never used. This is an independent implementation
of the same rules the final Verification stage audits.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from itertools import count
from typing import Callable, Iterable, Mapping

from utils import CampusGraph, GraphEdge

from .routing_core import K as COMFORT_WEIGHT


@dataclass(frozen=True)
class RoutePlan:
    steps: tuple[tuple[str, str, str], ...]   # (from node, to node, edge id) in walking order
    policy: str                                # "safe_route" or "coolest_fallback"
    travel_time_s: float
    walking_time_s: float
    waiting_time_s: float
    cumulative_exposure_degree_min: float
    maximum_exposure_rate_c: float
    maximum_utci_c: float
    fastest_time_s: float
    within_time_bound: bool
    note: str = ""


class PolicyRouter:
    """Directional route search over one round's graph and heat."""

    def __init__(self, graph: CampusGraph, blocked: Mapping[str, float], maximum_arrival_ratio: float):
        if not math.isfinite(maximum_arrival_ratio) or maximum_arrival_ratio < 1:
            raise ValueError("maximum_arrival_ratio must be finite and at least 1")
        self.ratio = maximum_arrival_ratio
        self.edges = {edge.edge_id: edge for edge in graph.edges}
        self.adjacency: dict[str, list[tuple[str, GraphEdge]]] = {node: [] for node in graph.nodes}
        for edge in graph.edges:
            if edge.accessible and not blocked.get(edge.edge_id, 0.0):
                self.adjacency[edge.from_node].append((edge.to_node, edge))
                self.adjacency[edge.to_node].append((edge.from_node, edge))
        self.waits: dict[tuple[str, str], float] = {}
        for actuator in graph.actuators.values():
            if actuator.actuator_type != "traffic_signal":
                continue
            wait = actuator.properties.get("wait_time_s")
            if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait < 0:
                raise ValueError(f"{actuator.actuator_id}: wait_time_s must be a finite nonnegative number")
            for edge_id in actuator.controlled_edges:
                key = (actuator.anchor_node, edge_id)
                self.waits[key] = max(self.waits.get(key, 0.0), float(wait))

    # -- per-step quantities -------------------------------------------------
    def _durations(self, node: str, edge: GraphEdge, speed: float) -> tuple[float, float]:
        return edge.distance_m / speed, self.waits.get((node, edge.edge_id), 0.0)

    @staticmethod
    def _rate(edge: GraphEdge) -> float:
        return float(edge.properties["heat_exposure_rate"])

    @staticmethod
    def _safe(edge: GraphEdge) -> bool:
        return edge.properties["unsafe_heat"] == 0.0

    # -- generic search --------------------------------------------------------
    def _search(self, start: str, targets: Iterable[str],
                cost: Callable[[str, GraphEdge], tuple[float, ...]],
                allowed: Callable[[GraphEdge], bool] = lambda edge: True):
        """Lexicographic additive Dijkstra; returns the steps to the first target."""
        targets = set(targets)
        serial = count()
        zero = tuple(0.0 for _ in cost(start, next(iter(self.edges.values()))))
        best, parent = {start: zero}, {}
        queue = [(zero, next(serial), start)]
        while queue:
            value, _, node = heapq.heappop(queue)
            if value != best[node]:
                continue
            if node in targets:
                steps = []
                while node != start:
                    previous, edge = parent[node]
                    steps.append((previous, node, edge.edge_id))
                    node = previous
                return tuple(reversed(steps))
            for neighbor, edge in self.adjacency[node]:
                if not allowed(edge):
                    continue
                candidate = tuple(a + b for a, b in zip(value, cost(node, edge)))
                if neighbor not in best or candidate < best[neighbor]:
                    best[neighbor] = candidate
                    parent[neighbor] = (node, edge)
                    heapq.heappush(queue, (candidate, next(serial), neighbor))
        return None

    def summarize(self, steps, speed: float) -> dict[str, float]:
        walking = waiting = exposure = 0.0
        hottest, hottest_utci = 0.0, -math.inf
        for node, _, edge_id in steps:
            edge = self.edges[edge_id]
            walk, wait = self._durations(node, edge, speed)
            walking += walk
            waiting += wait
            exposure += self._rate(edge) * (walk + wait) / 60.0
            hottest = max(hottest, self._rate(edge))
            hottest_utci = max(hottest_utci, float(edge.properties.get("utci_c", -math.inf)))
        return {"travel_time_s": walking + waiting, "walking_time_s": walking,
                "waiting_time_s": waiting, "cumulative_exposure_degree_min": exposure,
                "maximum_exposure_rate_c": hottest, "maximum_utci_c": hottest_utci}

    # -- policy ----------------------------------------------------------------
    def plan(self, start: str, targets: Iterable[str], speed: float, preference: float,
             walkability: Mapping[str, float], *, force_fallback: bool = False,
             prefix_time_s: float = 0.0) -> RoutePlan | None:
        """Plan from ``start``. When replanning mid-trip, ``prefix_time_s`` is the
        unavoidable time to finish the current edge, and ``force_fallback`` is
        set when that edge is unsafe (so no fully safe journey exists)."""
        targets = list(targets)
        if start in targets:
            return RoutePlan((), "already_at_destination", 0, 0, 0, 0, 0, 0, 0, True)

        def time_cost(node, edge):
            walk, wait = self._durations(node, edge, speed)
            return (walk + wait,)

        def comfort_cost(node, edge):
            factor = 1 + COMFORT_WEIGHT * preference * (1 - walkability[edge.edge_id])
            return (edge.distance_m * factor, sum(self._durations(node, edge, speed)))

        def exposure_cost(node, edge):
            walk, wait = self._durations(node, edge, speed)
            return (self._rate(edge) * (walk + wait) / 60.0, walk + wait)

        fastest = self._search(start, targets, time_cost)
        if fastest is None:
            return None
        fastest_time = self.summarize(fastest, speed)["travel_time_s"]
        # The bound covers the whole remaining journey, including the prefix.
        limit = self.ratio * (prefix_time_s + fastest_time) - prefix_time_s + 1e-6

        safe = None if force_fallback else self._search(start, targets, comfort_cost, self._safe)
        if safe is not None:
            policy, note = "safe_route", ""
            if self.summarize(safe, speed)["travel_time_s"] > limit:
                safe = self._search(start, targets, time_cost, self._safe)
                note = "preferred safe route exceeded the time bound; fastest safe route used"
            steps = safe
        else:
            policy, note = "coolest_fallback", "no safe route; least cumulative exposure"
            steps = self._search(start, targets, exposure_cost)
        summary = self.summarize(steps, speed)
        if summary["travel_time_s"] > limit:
            note = (note + "; " if note else "") + "exceeds the time bound (safety/coolness takes precedence)"
        return RoutePlan(steps, policy, fastest_time_s=fastest_time,
                         within_time_bound=summary["travel_time_s"] <= limit, note=note, **summary)
