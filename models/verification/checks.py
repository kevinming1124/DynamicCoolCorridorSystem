"""Current-condition route policy checks, without forecasts or route mutations."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from itertools import count
from utils import CampusGraph, GraphEdge

PASS, FAIL, NA, INCONCLUSIVE = "PASS", "FAIL", "NOT_APPLICABLE", "INCONCLUSIVE"
CODES = {PASS: 1.0, FAIL: 0.0, NA: -1.0, INCONCLUSIVE: -2.0}


def number(value, name, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def aggregate(statuses):
    statuses = set(statuses)
    return next((s for s in (FAIL, INCONCLUSIVE, PASS) if s in statuses), NA)


@dataclass(frozen=True)
class Segment:
    edge: GraphEdge
    start: str
    end: str
    walking_s: float
    waiting_s: float

    @property
    def duration_s(self):
        return self.walking_s + self.waiting_s

    @property
    def rate(self):
        return self.edge.properties["heat_exposure_rate"]

    @property
    def exposure(self):
        # The outgoing edge approximates conditions at the waiting location.
        return self.rate * self.duration_s / 60.0

    @property
    def safe(self):
        # Heat Exposure owns the threshold, including its exact >= boundary.
        return self.edge.properties["unsafe_heat"] == 0.0


def metrics(segments):
    return {
        "maximum_exposure_rate_c": max((s.rate for s in segments), default=0.0),
        "cumulative_exposure_degree_min": sum(s.exposure for s in segments),
        "travel_time_s": sum(s.duration_s for s in segments),
        "walking_time_s": sum(s.walking_s for s in segments),
        "waiting_time_s": sum(s.waiting_s for s in segments),
    }


class RouteNetwork:
    """Directional costs over the shared bidirectional campus graph."""
    def __init__(self, graph: CampusGraph):
        self.graph = graph
        self.edges_by_pair = {}
        self.adjacency = {node: [] for node in graph.nodes}
        self.waits = {}
        for actuator in graph.actuators.values():
            if actuator.actuator_type != "traffic_signal":
                continue
            wait = number(actuator.properties.get("wait_time_s"),
                          f"{actuator.actuator_id}.wait_time_s", minimum=0)
            for edge_id in actuator.controlled_edges:
                key = (actuator.anchor_node, edge_id)
                self.waits[key] = max(self.waits.get(key, 0.0), wait)
        for edge in graph.edges:
            pair = frozenset((edge.from_node, edge.to_node))
            if pair in self.edges_by_pair:
                raise ValueError("Verification requires unambiguous node-pair routes")
            self.edges_by_pair[pair] = edge
            if number(edge.distance_m, f"{edge.edge_id}.distance_m", minimum=0) <= 0:
                raise ValueError(f"{edge.edge_id}: distance must be positive")
            if self.available(edge):
                self.adjacency[edge.from_node].append((edge.to_node, edge))
                self.adjacency[edge.to_node].append((edge.from_node, edge))

    @staticmethod
    def available(edge):
        return edge.accessible and edge.properties.get("blocked", 0.0) == 0.0

    def segment(self, edge, start, end, speed, *, distance=None, wait=None):
        distance = edge.distance_m if distance is None else distance
        wait = self.waits.get((start, edge.edge_id), 0.0) if wait is None else wait
        return Segment(edge, start, end, distance / speed, wait)

    def remaining(self, person):
        nodes, index = person["route"], person["current_edge_index"]
        if (not isinstance(nodes, list) or len(nodes) < 2
                or nodes[0] != person["origin"] or nodes[-1] != person["destination"]
                or any(node not in self.graph.nodes for node in nodes)):
            raise ValueError("Invalid pedestrian route endpoints")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(nodes) - 1:
            raise ValueError("Invalid current_edge_index")
        speed = number(person["walking_speed_mps"], "walking_speed_mps", minimum=0)
        if speed <= 0:
            raise ValueError("walking_speed_mps must be positive")
        position = number(person["position_m"], "position_m", minimum=0)
        countdown = number(person["signal_wait_remaining_sec"], "signal_wait_remaining_sec", minimum=0)
        result, violations = [], []
        for offset in range(index, len(nodes) - 1):
            start, end = nodes[offset:offset + 2]
            edge = self.edges_by_pair.get(frozenset((start, end)))
            if edge is None:
                raise ValueError(f"Route segment does not exist: {start} -> {end}")
            if not self.available(edge):
                violations.append(f"{edge.edge_id}: route edge is inaccessible or blocked")
            distance, wait = edge.distance_m, None
            if offset == index:
                if position > distance:
                    raise ValueError("position_m exceeds current edge length")
                distance -= position
                if position > 0:
                    if countdown > 0:
                        raise ValueError("A moving pedestrian cannot have a signal countdown")
                    wait = 0.0
                elif person["signal_wait_edge_index"] == index:
                    wait = countdown
                elif countdown > 0:
                    raise ValueError("Countdown does not match the current departure")
            result.append(self.segment(edge, start, end, speed, distance=distance, wait=wait))
        # Once entered or a wait was sampled, comparisons must finish that edge.
        committed = position > 0 or person["signal_wait_edge_index"] == index
        prefix = result[:1] if committed else []
        start = result[0].end if committed else result[0].start
        return result, prefix, start, speed, violations

    def search(self, start, destination, speed, *, mode, ceiling=None, safe_only=False):
        """Dijkstra with time, bottleneck, or additive exposure/time costs.

        Minimax and additive optimization use separate passes: one lexicographic
        label cannot safely discard intermediate bottlenecks.
        """
        serial = count()
        targets = {destination} if isinstance(destination, str) else set(destination)
        zero = (0.0, 0.0) if mode == "exposure" else (0.0,)
        best, parent = {start: zero}, {}
        queue = [(zero, next(serial), start)]
        while queue:
            cost, _, node = heapq.heappop(queue)
            if cost != best[node]:
                continue
            if node in targets:
                path = []
                while node != start:
                    previous, segment = parent[node]
                    path.append(segment)
                    node = previous
                return list(reversed(path))
            for neighbor, edge in self.adjacency[node]:
                segment = self.segment(edge, node, neighbor, speed)
                if safe_only and not segment.safe:
                    continue
                if ceiling is not None and segment.rate > ceiling:
                    continue
                if mode == "time":
                    candidate = (cost[0] + segment.duration_s,)
                elif mode == "bottleneck":
                    candidate = (max(cost[0], segment.rate),)
                else:
                    candidate = (cost[0] + segment.exposure, cost[1] + segment.duration_s)
                if neighbor not in best or candidate < best[neighbor]:
                    best[neighbor] = candidate
                    parent[neighbor] = (node, segment)
                    heapq.heappush(queue, (candidate, next(serial), neighbor))
        return None


def audit_route(network, person, monitor, maximum_ratio, tolerance, destinations=None):
    pid = person["pedestrian_id"]
    if person["state"] == "arrived":
        return {"pedestrian_id": pid, "status": NA, "reason": "already_arrived",
                "route_selection_status": NA, "bounded_arrival_status": NA, "fallback_status": NA}
    segments, prefix, start, speed, violations = network.remaining(person)
    destination = destinations or person["destination"]
    if destinations is not None and (
            not isinstance(destinations, (list, tuple)) or not destinations
            or any(node not in network.graph.nodes for node in destinations)
            or person["destination"] not in destinations):
        raise ValueError("Invalid resolved destination entrances")
    fastest_tail = network.search(start, destination, speed, mode="time")
    safe_tail = network.search(start, destination, speed, mode="time", safe_only=True)
    safe_exists = all(s.safe for s in prefix) and safe_tail is not None
    route_safe = monitor.evaluate(tuple(s.edge for s in segments))
    selected = metrics(segments)
    record = {"pedestrian_id": pid, "scope": "remaining_journey",
              "edge_ids": [s.edge.edge_id for s in segments],
              "safe_route_exists": safe_exists, "selected_route_safe": route_safe,
              "selected": selected, "violations": violations,
              "maximum_utci_c": max(s.edge.properties["utci_c"] for s in segments),
              "comparison_start_node": start, "committed_prefix_edges": [s.edge.edge_id for s in prefix]}
    if fastest_tail is None:
        record.update(status=FAIL, route_selection_status=FAIL, bounded_arrival_status=NA,
                      fallback_status=NA, reason="no_available_path")
        return record
    fastest = metrics(prefix + fastest_tail)["travel_time_s"]
    arrival_valid = selected["travel_time_s"] <= maximum_ratio * fastest + tolerance
    record.update(fastest_time_s=fastest, maximum_allowed_time_s=maximum_ratio * fastest,
                  bounded_arrival_status=PASS if arrival_valid else FAIL,
                  fallback_status=NA, optimal_fallback=None)
    if safe_exists:
        selection_valid = route_safe and not violations
        record["reason"] = "safe_route_required"
    else:
        bottleneck_tail = network.search(start, destination, speed, mode="bottleneck")
        ceiling = metrics(prefix + bottleneck_tail)["maximum_exposure_rate_c"]
        optimal_tail = network.search(start, destination, speed, mode="exposure", ceiling=ceiling)
        optimal = metrics(prefix + optimal_tail)
        keys = ("maximum_exposure_rate_c", "cumulative_exposure_degree_min", "travel_time_s")
        selection_valid = not violations and all(
            math.isclose(selected[k], optimal[k], rel_tol=0.0, abs_tol=tolerance) for k in keys)
        record.update(fallback_status=PASS if selection_valid else FAIL,
                      optimal_fallback={**optimal, "edge_ids": [s.edge.edge_id for s in prefix + optimal_tail]},
                      reason="coolest_fallback_required")
    if not selection_valid:
        violations.append("Selected route does not satisfy the safe-first/coolest-fallback policy")
    externally_paused = person["state"] == "waiting" and person["signal_wait_remaining_sec"] == 0
    if not arrival_valid and not externally_paused:
        violations.append("Remaining journey exceeds the independent arrival bound")
    record["route_selection_status"] = PASS if selection_valid else FAIL
    record["route_policy_outcome"] = (
        "SAFE_ROUTE" if selection_valid and safe_exists else
        "COOLEST_FALLBACK" if selection_valid else "POLICY_VIOLATION")
    record["status"] = aggregate((record["route_selection_status"], record["bounded_arrival_status"]))
    if externally_paused:
        record["bounded_arrival_status"] = INCONCLUSIVE
        record["status"] = aggregate((record["route_selection_status"], INCONCLUSIVE))
        record["arrival_note"] = "External pause duration unknown; estimate assumes immediate release"
    return record
