"""Adapt routingc's actual search/cost code to the shared graph pipeline.

No OSMnx or plotting dependencies are imported at runtime. The graph view only
translates CampusGraph to the edge interface read by routingc's unchanged search.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .policy import PolicyRouter
from .routing_core import make_cost
from utils import CampusGraph, ModelInput, ModelOutput
from models.pedestrian.agent import PedestrianState
from models.pedestrian.model import PedestrianSimulator

DEFAULT_CONFIG = Path(__file__).with_name("config.json")
OUTPUTS = ("walkability", "blocked", "planned_pedestrians")


def finite_number(value, name, *, minimum=0.0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < minimum or (positive and value == minimum):
        raise ValueError(f"Invalid {name}: {value}")
    return float(value)


@dataclass(frozen=True)
class TripRequest:
    pedestrian_id: int
    origin: str
    destination: str
    preference: float = 0.0
    walking_speed_mps: float = 1.4

    def __post_init__(self):
        if isinstance(self.pedestrian_id, bool) or not isinstance(self.pedestrian_id, int):
            raise ValueError("pedestrian_id must be an integer")
        if any(not isinstance(s, str) or not s.strip() for s in (self.origin, self.destination)):
            raise ValueError("Origin and destination must be nonempty names or node IDs")
        if finite_number(self.preference, "preference") > 1:
            raise ValueError("preference must be between zero and one")
        finite_number(self.walking_speed_mps, "walking_speed_mps", positive=True)

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or "pedestrian_id" not in record:
            raise ValueError("Each trip requires a pedestrian_id")
        if "origin" in record and "destination" in record:
            origin, destination = record["origin"], record["destination"]
        else:
            # Compatibility with --routes: extract demand, not a fixed path.
            route = record.get("route")
            if not isinstance(route, list) or len(route) < 2:
                raise ValueError("Each trip requires origin/destination or a legacy route")
            origin, destination = route[0], route[-1]
        return cls(record["pedestrian_id"], origin, destination,
                   record.get("preference", 0.0), record.get("walking_speed_mps", 1.4))


def requests_from_records(records):
    if not isinstance(records, list):
        raise ValueError("Trips must be an array")
    requests = tuple(TripRequest.from_record(record) for record in records)
    if len({r.pedestrian_id for r in requests}) != len(requests):
        raise ValueError("Duplicate pedestrian_id in trip requests")
    return requests


def load_trip_requests(path: str | Path) -> tuple[TripRequest, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Trip file must be a JSON object")
    return requests_from_records(raw.get("pedestrians"))


class CampusRoutingView:
    """The NetworkX-style edge iterator required by routingc's Dijkstra.

    Parallel node pairs are rejected because the pedestrian's node routes
    cannot distinguish them. The OSM exporter splits such walkways into unique
    vertex chains instead of dropping any source walkway.
    """
    def __init__(self, graph: CampusGraph, walkability: Mapping[str, float], blocked: Mapping[str, float]):
        self.adjacency = {node: [] for node in graph.nodes}
        pairs = set()
        for edge in graph.edges:
            pair = frozenset((edge.from_node, edge.to_node))
            if pair in pairs:
                raise ValueError("Parallel node pairs require conversion before pedestrian routing")
            pairs.add(pair)
            if not edge.accessible:
                continue
            length = finite_number(edge.distance_m, "edge distance", positive=True)
            data = {"length": length, "walkability": walkability[edge.edge_id],
                    "blocked": bool(blocked[edge.edge_id])}
            self.adjacency[edge.from_node].append((edge.from_node, edge.to_node, edge.edge_id, data))
            self.adjacency[edge.to_node].append((edge.to_node, edge.from_node, edge.edge_id, data))

    def edges(self, node, *, keys=True, data=True):
        return iter(self.adjacency[node])


class RoutingModel:
    def __init__(self, config: Mapping[str, Any], simulator: PedestrianSimulator,
                 requests: Sequence[TripRequest] | None = None,
                 buildings: Mapping[str, Sequence[str]] | None = None):
        if config.get("property_target") != "edge" or tuple(config.get("output_properties", ())) != OUTPUTS:
            raise ValueError("Routing config must declare its three complete edge outputs")
        if not isinstance(config.get("model_name"), str) or not config["model_name"].strip():
            raise ValueError("Routing config requires a model name")
        if not isinstance(config.get("enabled"), bool):
            raise ValueError("Routing config enabled must be Boolean")
        self.config = config
        self.scale = finite_number(config["parameters"]["discomfort_scale_c"], "discomfort_scale_c", positive=True)
        self.maximum_arrival_ratio = finite_number(
            config["parameters"].get("maximum_arrival_ratio", 2.5), "maximum_arrival_ratio", minimum=1.0)
        self.simulator = simulator
        self.buildings = dict(buildings or {})
        if requests is not None:
            self.pending = list(requests)
        elif simulator.pedestrians:
            self.pending = [TripRequest(p.pedestrian_id, p.origin, p.destination,
                                        walking_speed_mps=p.walking_speed_mps)
                            for p in simulator.pedestrians.values()
                            if p.state != PedestrianState.ARRIVED and p.current_edge_index == 0
                            and p.position_m == 0 and p.total_travel_time_sec == 0
                            and p.total_wait_time_sec == 0 and p.signal_wait_remaining_sec == 0]
        else:
            self.pending = list(requests_from_records(config.get("metadata", {}).get("trips", [])))
        if any(not isinstance(r, TripRequest) for r in self.pending):
            raise ValueError("requests must contain TripRequest objects")
        if len({r.pedestrian_id for r in self.pending}) != len(self.pending):
            raise ValueError("Duplicate pedestrian IDs")
        self.assigned_routes = []
        # Trip settings kept for replanning walking pedestrians in later rounds.
        self.trips: dict[int, dict[str, Any]] = {}
        self.replan_history: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, simulator, requests=None, buildings=None, path=DEFAULT_CONFIG):
        return cls(json.loads(Path(path).read_text()), simulator, requests, buildings)

    def _replan(self, simulator, router, walkability, edges_by_id, skip_ids, usage):
        """Re-check walking pedestrians' remaining routes against current heat.

        Each round, a pedestrian assigned by this stage gets its remaining route
        recomputed with the same policy from its replanning node (the end of a
        committed current edge, otherwise its current node). The route is
        replaced only when the result differs. A committed unsafe current edge
        means no fully safe journey remains, so the coolest fallback is used.
        """
        changes = []
        for pid, agent in sorted(simulator.pedestrians.items()):
            trip = self.trips.get(pid)
            if pid in skip_ids or trip is None or agent.state == PedestrianState.ARRIVED:
                continue
            keep = simulator.replanning_node_index(pid)
            start = agent.route[keep]
            if start in trip["targets"]:
                continue
            speed = agent.walking_speed_mps
            prefix_time, prefix_safe = 0.0, True
            if keep > agent.current_edge_index:
                edge = edges_by_id[self._edge_between(agent.route[keep - 1], start)]
                prefix_time = (edge.distance_m - agent.position_m) / speed + agent.signal_wait_remaining_sec
                prefix_safe = edge.properties["unsafe_heat"] == 0.0
            plan = router.plan(start, trip["targets"], speed, trip["preference"], walkability,
                               force_fallback=not prefix_safe, prefix_time_s=prefix_time)
            if plan is None or not plan.steps:
                continue
            tail = [start] + [step[1] for step in plan.steps]
            if tail == agent.route[keep:]:
                continue
            simulator.replace_remaining_route(pid, tail)
            for step in plan.steps:
                usage[step[2]] += 1.0
            changes.append({"pedestrian_id": pid, "from_node": start,
                            "previous_tail": agent.route[keep:], "new_tail": tail,
                            "route_policy": plan.policy, "policy_note": plan.note,
                            "simulation_time_sec": simulator.simulation_time_sec})
        return changes

    def _edge_between(self, a, b):
        return self._pair_index[frozenset((a, b))]

    def resolve(self, graph, name):
        if name in graph.nodes:
            return [name]
        result = list(self.buildings.get(name, ()))
        if not result:
            result = [n.node_id for n in graph.nodes.values() if n.location_name == name]
        if not result or any(node not in graph.nodes for node in result):
            raise ValueError(f"Unknown building or node in this dataset: {name}")
        return result

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.config["enabled"]:
            raise RuntimeError("Routing is disabled")
        graph = model_input.graph
        rates, walkability, blocked = {}, {}, {}
        for edge in graph.edges:
            rate = finite_number(edge.properties.get("heat_exposure_rate"), f"{edge.edge_id} heat_exposure_rate")
            rates[edge.edge_id] = rate
            walkability[edge.edge_id] = 1.0 - min(1.0, rate / self.scale)
            flag = edge.properties.get("blocked", 0.0)
            if isinstance(flag, bool) or not isinstance(flag, (int, float)) or flag not in (0.0, 1.0):
                raise ValueError(f"{edge.edge_id}: blocked must be numeric zero or one")
            blocked[edge.edge_id] = float(flag)
        for edge in graph.edges:
            flag = edge.properties.get("unsafe_heat")
            if isinstance(flag, bool) or flag not in (0.0, 1.0):
                raise ValueError(f"{edge.edge_id}: unsafe_heat must be numeric zero or one")
        CampusRoutingView(graph, walkability, blocked)  # validates unambiguous node pairs
        self._pair_index = {frozenset((e.from_node, e.to_node)): e.edge_id for e in graph.edges}
        router = PolicyRouter(graph, blocked, self.maximum_arrival_ratio)
        heat_report = (model_input.context or {}).get("stage_diagnostics", {}).get("heat_exposure_model", {})
        unsafe_utci_c = heat_report.get("unsafe_utci_c")
        candidate = PedestrianSimulator(graph)
        candidate.simulation_time_sec = self.simulator.simulation_time_sec
        for agent in self.simulator.pedestrians.values():
            candidate.add_pedestrian(agent)
        usage = {edge.edge_id: 0.0 for edge in graph.edges}
        edges_by_id = {edge.edge_id: edge for edge in graph.edges}
        plans, pending, unavailable, already_there = [], [], [], []
        pending_details = []
        for request in self.pending:
            existing = candidate.pedestrians.get(request.pedestrian_id)
            if existing is not None and (
                existing.state == PedestrianState.ARRIVED
                or existing.current_edge_index != 0
                or existing.position_m != 0
                or existing.total_travel_time_sec != 0
                or existing.total_wait_time_sec != 0
                or existing.signal_wait_remaining_sec != 0
            ):
                raise ValueError(f"P{request.pedestrian_id}: routing requests must be assigned before departure")
            start = self.resolve(graph, request.origin)[0]
            targets = self.resolve(graph, request.destination)
            plan = router.plan(start, targets, request.walking_speed_mps, request.preference, walkability)
            route = None if plan is None else list(plan.steps)
            if route is None:
                pending.append(request)
                unavailable.append(request.pedestrian_id)
                pending_details.append({"pedestrian_id": request.pedestrian_id,
                                        "origin_node": start, "destination_nodes": targets,
                                        "walking_speed_mps": request.walking_speed_mps})
                if request.pedestrian_id in candidate.pedestrians:
                    candidate.pedestrians[request.pedestrian_id].state = PedestrianState.WAITING
                continue
            if not route:
                if existing is not None:
                    raise ValueError("A zero-length replacement cannot preserve an existing agent's route")
                already_there.append(request.pedestrian_id)
                continue
            nodes = [route[0][0]] + [step[1] for step in route]
            ids = [step[2] for step in route]
            candidate.assign_route(request.pedestrian_id, nodes, walking_speed_mps=request.walking_speed_mps)
            candidate.pedestrians[request.pedestrian_id].state = PedestrianState.WALKING
            distance = sum(edges_by_id[e].distance_m for e in ids)
            exposure = sum(rates[e] * edges_by_id[e].distance_m / request.walking_speed_mps / 60 for e in ids)
            comfort = make_cost(request.preference)
            cost = sum(comfort(u, v, {"length": edges_by_id[e].distance_m, "walkability": walkability[e]})
                       for u, v, e in route)
            relaxation = (max(0.0, plan.maximum_utci_c - unsafe_utci_c)
                          if plan.policy == "coolest_fallback" and unsafe_utci_c is not None else None)
            plans.append({"pedestrian_id": request.pedestrian_id,
                          "origin": request.origin, "destination": request.destination,
                          "preference": request.preference, "nodes": nodes, "edges": ids,
                          "destination_nodes": targets,
                          "distance_m": distance, "weighted_cost_m": cost,
                          "estimated_walk_minutes": distance / request.walking_speed_mps / 60,
                          "estimated_walking_exposure_degree_min": exposure,
                          "route_policy": plan.policy,
                          "estimated_travel_minutes_with_waits": plan.travel_time_s / 60,
                          "estimated_exposure_with_waits_degree_min": plan.cumulative_exposure_degree_min,
                          "maximum_utci_c": plan.maximum_utci_c,
                          "required_threshold_relaxation_c": relaxation,
                          "fastest_minutes": plan.fastest_time_s / 60,
                          "within_time_bound": plan.within_time_bound,
                          "policy_note": plan.note})
            for edge_id in ids:
                usage[edge_id] += 1.0
        new_ids = {plan["pedestrian_id"] for plan in plans}
        replanned = self._replan(candidate, router, walkability, edges_by_id, new_ids, usage)
        values = {"walkability": walkability, "blocked": blocked, "planned_pedestrians": usage}
        updated = graph
        for name, mapping in values.items():
            updated = updated.with_edge_property(name, mapping)
        output = ModelOutput(
            model_name=self.config["model_name"], property_target="edge",
            property_names=OUTPUTS, property_values=values, graph=updated,
            diagnostics={"algorithm": "safe-first policy: routingc make_cost on safe walkways, "
                                      "else least-exposure coolest fallback",
                         "maximum_arrival_ratio": self.maximum_arrival_ratio,
                         "heat_source": "pipeline heat_exposure_rate",
                         "walkability_mapping": "1 - min(1, heat_exposure_rate / discomfort_scale_c)",
                         "discomfort_scale_c": self.scale, "routes": plans,
                         "all_assigned_routes": self.assigned_routes + plans,
                         "pending_trip_ids": unavailable,
                         "pending_requests": pending_details,
                         "already_at_destination_ids": already_there,
                         "replanned_routes": replanned,
                         "replan_count_total": len(self.replan_history)},
        )
        self.simulator.graph = updated
        self.simulator.pedestrians = candidate.pedestrians
        self.pending = pending
        self.assigned_routes.extend(plans)
        for plan in plans:
            self.trips[plan["pedestrian_id"]] = {
                "targets": plan["destination_nodes"], "preference": plan["preference"],
                "walking_speed_mps": candidate.pedestrians[plan["pedestrian_id"]].walking_speed_mps}
        self.replan_history.extend(replanned)
        print(f"Routing (routingc): {len(plans)} new routes -> Pedestrian; {len(pending)} pending"
              + (f"; {len(replanned)} replanned" if replanned else ""))
        for change in replanned:
            print(f"  P{change['pedestrian_id']} replanned: {change['route_policy'].replace('_', ' ')}")
        for plan in plans:
            label = plan["route_policy"].replace("_", " ")
            if plan["required_threshold_relaxation_c"] is not None:
                label += f", {plan['required_threshold_relaxation_c']:.1f} C over the unsafe line"
            print(f"  P{plan['pedestrian_id']}: {plan['origin']} -> {plan['destination']}; "
                  f"preference {plan['preference']:.2f}, {plan['distance_m']:.1f} m, "
                  f"estimated walk {plan['estimated_walk_minutes']:.2f} min; {label}")
        return output
