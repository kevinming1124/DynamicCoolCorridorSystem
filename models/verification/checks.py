"""Python checks used by the final requirements verification model."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any

from utils import CampusGraph, GraphEdge, StaticActuator


ROUTE_ORDER = "route_order"
EXPECTED_ENTRY_TIME = "expected_entry_time_s"
PREDICTED_EXPOSURE = "predicted_exposure_at_entry"
PREDICTED_TRAVEL_TIME = "predicted_travel_time_s"
FALLBACK_SELECTED = "fallback_selected"
REQUIRED_RELAXATION = "required_threshold_relaxation"
BASELINE_TEMPERATURE = "baseline_temperature_f"
MISTING_TEMPERATURE_EFFECT = "misting_temperature_effect_f"
CONTROLLED_TEMPERATURE = "controlled_temperature_f"
BASELINE_EXPOSURE = "baseline_predicted_exposure_at_entry"
MISTING_EXPOSURE_EFFECT = "misting_exposure_effect_at_entry"
BASELINE_TRAVEL_TIME = "baseline_travel_time_s"
SIGNAL_TRAVEL_TIME_EFFECT = "signal_travel_time_effect_s"
PEDESTRIAN_DEMAND = "pedestrian_demand"


@dataclass(frozen=True)
class PathMetrics:
    edge_ids: tuple[str, ...]
    travel_time_s: float
    maximum_exposure: float
    cumulative_exposure: float


@dataclass(frozen=True)
class ArrivalCheck:
    valid: bool
    selected_time_s: float
    shortest_time_s: float
    maximum_allowed_time_s: float
    ratio: float


@dataclass(frozen=True)
class FallbackCheck:
    valid: bool
    safe_bounded_route_exists: bool
    expected_relaxation: float
    optimal_path: PathMetrics
    reason: str


@dataclass(frozen=True)
class ActuatorCheck:
    valid: bool
    violations: tuple[str, ...]
    changed_temperature_edges: tuple[str, ...]
    changed_exposure_edges: tuple[str, ...]
    changed_travel_time_edges: tuple[str, ...]


def edge_value(edge: GraphEdge, name: str) -> float:
    value = edge.properties.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{edge.edge_id}: required numeric property '{name}' is missing")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{edge.edge_id}: property '{name}' must be finite")
    return numeric


def selected_route(
    graph: CampusGraph, origin: str, destination: str
) -> tuple[GraphEdge, ...]:
    selected: list[tuple[int, GraphEdge]] = []
    for edge in graph.edges:
        order = edge_value(edge, ROUTE_ORDER)
        if order < 0.0:
            continue
        if not order.is_integer():
            raise ValueError(f"{edge.edge_id}: route_order must be an integer")
        if not edge.accessible:
            raise ValueError(f"{edge.edge_id}: selected route edge is inaccessible")
        selected.append((int(order), edge))

    selected.sort(key=lambda item: item[0])
    if not selected:
        raise ValueError("The selected route is empty")
    if [order for order, _ in selected] != list(range(len(selected))):
        raise ValueError("Selected route_order values must start at 0 and be contiguous")

    current = origin
    ordered: list[GraphEdge] = []
    visited_nodes = {origin}
    for _, edge in selected:
        if edge.from_node == current:
            current = edge.to_node
        elif edge.to_node == current:
            current = edge.from_node
        else:
            raise ValueError(f"{edge.edge_id}: selected route is not continuous")
        if current in visited_nodes and current != destination:
            raise ValueError("Selected route contains a cycle")
        visited_nodes.add(current)
        ordered.append(edge)
    if current != destination:
        raise ValueError("Selected route does not end at the destination")

    for edge in ordered:
        if edge_value(edge, EXPECTED_ENTRY_TIME) < 0.0:
            raise ValueError(
                f"{edge.edge_id}: selected edge requires a non-negative entry time"
            )
    return tuple(ordered)


def route_metrics(route: tuple[GraphEdge, ...]) -> PathMetrics:
    exposures = [edge_value(edge, PREDICTED_EXPOSURE) for edge in route]
    return PathMetrics(
        edge_ids=tuple(edge.edge_id for edge in route),
        travel_time_s=sum(edge_value(edge, PREDICTED_TRAVEL_TIME) for edge in route),
        maximum_exposure=max(exposures),
        cumulative_exposure=sum(exposures),
    )


def _adjacency(graph: CampusGraph) -> dict[str, list[tuple[str, GraphEdge]]]:
    adjacency = {node_id: [] for node_id in graph.nodes}
    for edge in graph.edges:
        if not edge.accessible:
            continue
        adjacency[edge.from_node].append((edge.to_node, edge))
        adjacency[edge.to_node].append((edge.from_node, edge))
    return adjacency


def shortest_travel_time(graph: CampusGraph, origin: str, destination: str) -> float:
    queue = [(0.0, origin)]
    best = {origin: 0.0}
    adjacency = _adjacency(graph)
    while queue:
        cost, node_id = heapq.heappop(queue)
        if cost > best.get(node_id, float("inf")):
            continue
        if node_id == destination:
            return cost
        for neighbor, edge in adjacency[node_id]:
            weight = edge_value(edge, PREDICTED_TRAVEL_TIME)
            if weight <= 0.0:
                raise ValueError(f"{edge.edge_id}: predicted travel time must be positive")
            candidate = cost + weight
            if candidate < best.get(neighbor, float("inf")):
                best[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))
    raise ValueError(f"No accessible path exists from {origin} to {destination}")


def check_arrival(
    graph: CampusGraph,
    route: tuple[GraphEdge, ...],
    origin: str,
    destination: str,
    maximum_ratio: float,
    tolerance: float,
) -> ArrivalCheck:
    selected_time = sum(edge_value(edge, PREDICTED_TRAVEL_TIME) for edge in route)
    shortest_time = shortest_travel_time(graph, origin, destination)
    maximum_allowed = maximum_ratio * shortest_time
    ratio = selected_time / shortest_time
    return ArrivalCheck(
        valid=selected_time <= maximum_allowed + tolerance,
        selected_time_s=selected_time,
        shortest_time_s=shortest_time,
        maximum_allowed_time_s=maximum_allowed,
        ratio=ratio,
    )


@dataclass(order=True)
class _PathLabel:
    maximum_exposure: float
    cumulative_exposure: float
    travel_time_s: float
    node_id: str
    visited_nodes: tuple[str, ...]
    edge_ids: tuple[str, ...]


def best_bounded_path(
    graph: CampusGraph,
    origin: str,
    destination: str,
    maximum_time_s: float,
    *,
    maximum_edge_exposure: float | None = None,
    tolerance: float = 0.0,
) -> PathMetrics | None:
    """Return the minimax path, then break ties by exposure sum and time."""
    adjacency = _adjacency(graph)
    queue = [
        _PathLabel(
            maximum_exposure=0.0,
            cumulative_exposure=0.0,
            travel_time_s=0.0,
            node_id=origin,
            visited_nodes=(origin,),
            edge_ids=(),
        )
    ]
    destination_labels: list[_PathLabel] = []

    while queue:
        label = heapq.heappop(queue)
        if label.node_id == destination:
            destination_labels.append(label)
            continue
        for neighbor, edge in adjacency[label.node_id]:
            if neighbor in label.visited_nodes:
                continue
            travel_time = edge_value(edge, PREDICTED_TRAVEL_TIME)
            if travel_time <= 0.0:
                raise ValueError(f"{edge.edge_id}: predicted travel time must be positive")
            candidate_time = label.travel_time_s + travel_time
            if candidate_time > maximum_time_s + tolerance:
                continue
            exposure = edge_value(edge, PREDICTED_EXPOSURE)
            if (
                maximum_edge_exposure is not None
                and exposure > maximum_edge_exposure + tolerance
            ):
                continue
            heapq.heappush(
                queue,
                _PathLabel(
                    maximum_exposure=max(label.maximum_exposure, exposure),
                    cumulative_exposure=label.cumulative_exposure + exposure,
                    travel_time_s=candidate_time,
                    node_id=neighbor,
                    visited_nodes=label.visited_nodes + (neighbor,),
                    edge_ids=label.edge_ids + (edge.edge_id,),
                ),
            )

    if not destination_labels:
        return None
    best = min(
        destination_labels,
        key=lambda item: (
            item.maximum_exposure,
            item.cumulative_exposure,
            item.travel_time_s,
            item.edge_ids,
        ),
    )
    return PathMetrics(
        edge_ids=best.edge_ids,
        travel_time_s=best.travel_time_s,
        maximum_exposure=best.maximum_exposure,
        cumulative_exposure=best.cumulative_exposure,
    )


def check_fallback(
    graph: CampusGraph,
    selected_metrics: PathMetrics,
    origin: str,
    destination: str,
    threshold: float,
    maximum_time_s: float,
    fallback_selected: bool,
    reported_relaxation: float,
    tolerance: float,
) -> FallbackCheck:
    safe_path = best_bounded_path(
        graph,
        origin,
        destination,
        maximum_time_s,
        maximum_edge_exposure=threshold,
        tolerance=tolerance,
    )
    optimal = best_bounded_path(
        graph,
        origin,
        destination,
        maximum_time_s,
        tolerance=tolerance,
    )
    if optimal is None:
        raise ValueError("No bounded accessible path exists")

    if safe_path is not None:
        valid = not fallback_selected and math.isclose(
            reported_relaxation, 0.0, abs_tol=tolerance
        )
        return FallbackCheck(
            valid=valid,
            safe_bounded_route_exists=True,
            expected_relaxation=0.0,
            optimal_path=safe_path,
            reason=(
                "normal safe route available"
                if valid
                else "fallback declared even though a bounded safe route exists"
            ),
        )

    expected_relaxation = max(0.0, optimal.maximum_exposure - threshold)
    selected_is_optimal = all(
        (
            math.isclose(
                selected_metrics.maximum_exposure,
                optimal.maximum_exposure,
                abs_tol=tolerance,
            ),
            math.isclose(
                selected_metrics.cumulative_exposure,
                optimal.cumulative_exposure,
                abs_tol=tolerance,
            ),
            math.isclose(
                selected_metrics.travel_time_s,
                optimal.travel_time_s,
                abs_tol=tolerance,
            ),
        )
    )
    valid = bool(
        fallback_selected
        and selected_is_optimal
        and math.isclose(
            reported_relaxation, expected_relaxation, abs_tol=tolerance
        )
    )
    return FallbackCheck(
        valid=valid,
        safe_bounded_route_exists=False,
        expected_relaxation=expected_relaxation,
        optimal_path=optimal,
        reason=(
            "correct bounded coolest fallback"
            if valid
            else "selected fallback or threshold relaxation is not optimal"
        ),
    )


def _actuator_number(actuator: StaticActuator, name: str) -> float:
    value = actuator.properties.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{actuator.actuator_id}: required numeric property '{name}' is missing"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{actuator.actuator_id}: property '{name}' must be finite")
    return numeric


def _actuator_string(actuator: StaticActuator, name: str) -> str:
    value = actuator.properties.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{actuator.actuator_id}: required string property '{name}' is missing"
        )
    return value.strip()


def _point_to_edge_distance(
    graph: CampusGraph, edge: GraphEdge, point_x: float, point_y: float
) -> float:
    start = graph.nodes[edge.from_node]
    end = graph.nodes[edge.to_node]
    dx = end.x_m - start.x_m
    dy = end.y_m - start.y_m
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return math.hypot(point_x - start.x_m, point_y - start.y_m)
    projection = (
        (point_x - start.x_m) * dx + (point_y - start.y_m) * dy
    ) / length_squared
    projection = min(1.0, max(0.0, projection))
    closest_x = start.x_m + projection * dx
    closest_y = start.y_m + projection * dy
    return math.hypot(point_x - closest_x, point_y - closest_y)


def check_actuator_response(
    graph: CampusGraph,
    *,
    misting_radius_m: float,
    maximum_exposure_reduction_per_f: float,
    minimum_controlled_temperature_f: float,
    supported_saturation_reasons: frozenset[str],
    tolerance: float,
) -> ActuatorCheck:
    violations: list[str] = []
    changed_temperature: set[str] = set()
    changed_exposure: set[str] = set()
    changed_time: set[str] = set()

    for edge in graph.edges:
        baseline_temperature = edge_value(edge, BASELINE_TEMPERATURE)
        temperature_effect = edge_value(edge, MISTING_TEMPERATURE_EFFECT)
        controlled_temperature = edge_value(edge, CONTROLLED_TEMPERATURE)
        baseline_exposure = edge_value(edge, BASELINE_EXPOSURE)
        exposure_effect = edge_value(edge, MISTING_EXPOSURE_EFFECT)
        controlled_exposure = edge_value(edge, PREDICTED_EXPOSURE)
        baseline_time = edge_value(edge, BASELINE_TRAVEL_TIME)
        signal_effect = edge_value(edge, SIGNAL_TRAVEL_TIME_EFFECT)
        controlled_time = edge_value(edge, PREDICTED_TRAVEL_TIME)

        if not math.isclose(
            baseline_temperature + temperature_effect,
            controlled_temperature,
            abs_tol=tolerance,
        ):
            violations.append(f"{edge.edge_id}: controlled temperature omits effect")
        if not math.isclose(
            baseline_exposure + exposure_effect,
            controlled_exposure,
            abs_tol=tolerance,
        ):
            violations.append(f"{edge.edge_id}: predicted exposure omits effect")
        if not math.isclose(
            baseline_time + signal_effect, controlled_time, abs_tol=tolerance
        ):
            violations.append(f"{edge.edge_id}: controlled travel time omits effect")
        if temperature_effect > tolerance or exposure_effect > tolerance:
            violations.append(f"{edge.edge_id}: misting increased heat")
        if abs(temperature_effect) > tolerance:
            changed_temperature.add(edge.edge_id)
        if abs(exposure_effect) > tolerance:
            changed_exposure.add(edge.edge_id)
        if abs(signal_effect) > tolerance:
            changed_time.add(edge.edge_id)

    misters = [
        actuator
        for actuator in graph.actuators.values()
        if actuator.actuator_type == "misting_point"
    ]
    for actuator in misters:
        status = _actuator_string(actuator, "status")
        commanded = _actuator_number(actuator, "commanded_temperature_reduction_f")
        applied = _actuator_number(actuator, "applied_temperature_reduction_f")
        saturated = _actuator_number(actuator, "effect_saturated")
        saturation_reason = _actuator_string(actuator, "saturation_reason")
        if saturation_reason not in supported_saturation_reasons:
            violations.append(
                f"{actuator.actuator_id}: unsupported saturation reason"
            )
        if status not in {"on", "off"}:
            violations.append(f"{actuator.actuator_id}: invalid misting status")
        if saturated not in {0.0, 1.0}:
            violations.append(f"{actuator.actuator_id}: effect_saturated must be 0 or 1")
        if not math.isclose(commanded, applied, abs_tol=tolerance):
            violations.append(f"{actuator.actuator_id}: commanded misting was not applied")
        assert actuator.max_heat_reduction_f is not None
        if not 0.0 <= applied <= actuator.max_heat_reduction_f:
            violations.append(f"{actuator.actuator_id}: misting command is out of bounds")
        if status == "off" and abs(applied) > tolerance:
            violations.append(f"{actuator.actuator_id}: inactive mister has a command")
        if saturated == 0.0 and saturation_reason != "none":
            violations.append(f"{actuator.actuator_id}: unexpected saturation reason")
        if saturated == 1.0 and saturation_reason != "physical_floor":
            violations.append(f"{actuator.actuator_id}: invalid misting saturation reason")

        local_edges = {
            edge.edge_id
            for edge in graph.edges
            if _point_to_edge_distance(graph, edge, actuator.x_m, actuator.y_m)
            <= misting_radius_m + tolerance
        }
        local_effect = False
        floor_evidence = False
        for edge in graph.edges:
            temperature_effect = edge_value(edge, MISTING_TEMPERATURE_EFFECT)
            exposure_effect = edge_value(edge, MISTING_EXPOSURE_EFFECT)
            if edge.edge_id not in local_edges and (
                abs(temperature_effect) > tolerance
                or abs(exposure_effect) > tolerance
            ):
                violations.append(
                    f"{edge.edge_id}: misting-attributed effect is outside "
                    f"{actuator.actuator_id} radius"
                )
            if edge.edge_id in local_edges:
                if (
                    abs(temperature_effect) > applied + tolerance
                    or abs(exposure_effect)
                    > applied * maximum_exposure_reduction_per_f + tolerance
                ):
                    violations.append(
                        f"{edge.edge_id}: misting-attributed effect exceeds command"
                    )
                local_effect = local_effect or (
                    abs(temperature_effect) > tolerance
                    or abs(exposure_effect) > tolerance
                )
                floor_evidence = floor_evidence or (
                    edge_value(edge, BASELINE_TEMPERATURE)
                    <= minimum_controlled_temperature_f + tolerance
                )
        if applied > tolerance and not local_effect:
            if not (
                saturated == 1.0
                and saturation_reason == "physical_floor"
                and floor_evidence
            ):
                violations.append(
                    f"{actuator.actuator_id}: nonzero misting produced no effect"
                )
        if applied <= tolerance and local_effect:
            violations.append(f"{actuator.actuator_id}: zero misting command had an effect")

    signals = [
        actuator
        for actuator in graph.actuators.values()
        if actuator.actuator_type == "traffic_signal"
    ]
    for actuator in signals:
        commanded = _actuator_number(actuator, "wait_time_s")
        applied = _actuator_number(actuator, "applied_wait_time_s")
        saturated = _actuator_number(actuator, "effect_saturated")
        saturation_reason = _actuator_string(actuator, "saturation_reason")
        if saturation_reason not in supported_saturation_reasons:
            violations.append(
                f"{actuator.actuator_id}: unsupported saturation reason"
            )
        assert actuator.default_wait_time_s is not None
        assert actuator.min_wait_time_s is not None
        assert actuator.max_wait_time_s is not None
        if not math.isclose(commanded, applied, abs_tol=tolerance):
            violations.append(f"{actuator.actuator_id}: commanded wait was not applied")
        if not actuator.min_wait_time_s <= applied <= actuator.max_wait_time_s:
            violations.append(f"{actuator.actuator_id}: signal command is out of bounds")
        if saturated not in {0.0, 1.0}:
            violations.append(f"{actuator.actuator_id}: effect_saturated must be 0 or 1")
        if saturated == 0.0 and saturation_reason != "none":
            violations.append(f"{actuator.actuator_id}: unexpected saturation reason")
        if saturated == 1.0 and saturation_reason != "no_demand":
            violations.append(f"{actuator.actuator_id}: invalid signal saturation reason")

        expected_direction = applied - actuator.default_wait_time_s
        controlled_effect = False
        no_demand = True
        for edge in graph.edges:
            signal_effect = edge_value(edge, SIGNAL_TRAVEL_TIME_EFFECT)
            if edge.edge_id not in actuator.controlled_edges:
                if abs(signal_effect) > tolerance:
                    violations.append(
                        f"{edge.edge_id}: signal effect is outside "
                        f"{actuator.actuator_id} controlled edges"
                    )
                continue
            no_demand = no_demand and edge_value(edge, PEDESTRIAN_DEMAND) <= tolerance
            if abs(signal_effect) > tolerance:
                controlled_effect = True
                if signal_effect * expected_direction <= 0.0:
                    violations.append(
                        f"{edge.edge_id}: signal effect has the wrong direction"
                    )
                if abs(signal_effect) > abs(expected_direction) + tolerance:
                    violations.append(
                        f"{edge.edge_id}: signal effect exceeds command change"
                    )
        if abs(expected_direction) > tolerance and not controlled_effect:
            if not (
                saturated == 1.0
                and saturation_reason == "no_demand"
                and no_demand
            ):
                violations.append(
                    f"{actuator.actuator_id}: adjusted signal produced no effect"
                )
        if abs(expected_direction) <= tolerance and controlled_effect:
            violations.append(f"{actuator.actuator_id}: unchanged signal had an effect")

    return ActuatorCheck(
        valid=not violations,
        violations=tuple(violations),
        changed_temperature_edges=tuple(sorted(changed_temperature)),
        changed_exposure_edges=tuple(sorted(changed_exposure)),
        changed_travel_time_edges=tuple(sorted(changed_time)),
    )
