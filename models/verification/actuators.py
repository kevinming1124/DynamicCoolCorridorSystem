"""Audit the implemented misting effects and signal departure/countdown rules."""
from __future__ import annotations

import math

from .checks import FAIL, NA, PASS, number


def segment_circle_coverage(x, y, radius, start, end):
    """Independently find the covered parameter interval via circle intersections."""
    dx, dy = end.x_m - start.x_m, end.y_m - start.y_m
    length_squared = dx * dx + dy * dy
    if radius <= 0 or length_squared == 0:
        return 0.0
    ox, oy = start.x_m - x, start.y_m - y
    b = 2.0 * (ox * dx + oy * dy)
    c = ox * ox + oy * oy - radius * radius
    discriminant = b * b - 4.0 * length_squared * c
    if discriminant <= 0:
        return 0.0
    root = math.sqrt(discriminant)
    entry = (-b - root) / (2.0 * length_squared)
    exit = (-b + root) / (2.0 * length_squared)
    return max(0.0, min(1.0, exit) - max(0.0, entry))


def audit_actuators(network, environment, before, after, dt, tolerance):
    graph = network.graph
    violations = []
    signal_command_violations = []
    radius = number(environment.get("mist_range_m"), "environment.mist_range_m", minimum=0)
    base_temp = number(environment.get("base_air_temp_f"), "environment.base_air_temp_f")
    base_humidity = number(environment.get("base_humidity_pct"), "environment.base_humidity_pct")
    humidity_increase = number(environment.get("mist_humidity_increase_pct"),
                               "environment.mist_humidity_increase_pct", minimum=0)
    on_misters = []
    for actuator in graph.actuators.values():
        if actuator.actuator_type == "misting_point":
            if actuator.properties.get("status") not in ("on", "off"):
                violations.append(f"{actuator.actuator_id}: invalid misting status")
            if actuator.properties.get("status") == "on":
                number(actuator.max_heat_reduction_f, f"{actuator.actuator_id}.max_heat_reduction_f", minimum=0)
                on_misters.append(actuator)
        elif actuator.actuator_type == "traffic_signal":
            wait = number(actuator.properties.get("wait_time_s"),
                          f"{actuator.actuator_id}.wait_time_s", minimum=0)
            low = number(actuator.min_wait_time_s, f"{actuator.actuator_id}.min_wait_time_s", minimum=0)
            high = number(actuator.max_wait_time_s, f"{actuator.actuator_id}.max_wait_time_s", minimum=0)
            if actuator.properties.get("status") != "on" or not low <= wait <= high:
                signal_command_violations.append(
                    f"{actuator.actuator_id}: signal command violates operating limits")
    maximum = max((a.max_heat_reduction_f for a in on_misters), default=0.0)
    for edge in graph.edges:
        start, end = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
        total = 0.0
        for actuator in on_misters:
            coverage = segment_circle_coverage(
                actuator.x_m, actuator.y_m, radius, start, end)
            total += actuator.max_heat_reduction_f * coverage
        cooling = min(total, maximum)
        humidity = base_humidity + (humidity_increase * cooling / maximum if maximum > 0 else 0.0)
        expected = {"mist_cooling_f": round(cooling, 2),
                    "air_temp_f": round(base_temp - cooling, 2),
                    "humidity_pct": round(min(100.0, max(0.0, humidity)), 2)}
        for name, value in expected.items():
            actual = number(edge.properties.get(name), f"{edge.edge_id}.{name}")
            if not math.isclose(actual, value, rel_tol=0.0, abs_tol=tolerance):
                violations.append(f"{edge.edge_id}: {name} inconsistent with same-round misting commands")

    misting_status = FAIL if violations else PASS
    signal_violations = audit_movement(network, before, after, dt, tolerance)
    violations.extend(signal_command_violations)
    violations.extend(signal_violations)
    return {"status": FAIL if violations else PASS, "violations": violations,
            "misting_status": misting_status,
            "signal_command_status": FAIL if signal_command_violations else PASS,
            "signal_movement_status": FAIL if signal_violations else PASS if before["pedestrians"] else NA,
            "thermal_exposure_reduction_status": NA,
            "thermal_exposure_reduction_note": "No no-misting heat comparison is calculated or claimed",
            "mist_range_m": radius,
            "evidence": "same-round Environment outputs, commands, and before/after pedestrian snapshots"}


def audit_movement(network, before, after, dt, tolerance):
    """Independently account for waiting and walking using the frozen round graph.

    Existing countdowns are retained, and new departures sample current commands.
    This does not move or replace any simulator agents.
    """
    violations = []
    elapsed = number(after["simulation_time_sec"], "after.simulation_time_sec") - number(
        before["simulation_time_sec"], "before.simulation_time_sec")
    if not math.isclose(elapsed, dt, rel_tol=0.0, abs_tol=tolerance):
        violations.append("Pedestrian clock did not advance by the requested timestep")
    after_by_id = {p["pedestrian_id"]: p for p in after["pedestrians"]}
    if set(after_by_id) != {p["pedestrian_id"] for p in before["pedestrians"]}:
        violations.append("Movement unexpectedly changed pedestrian membership")
        return violations
    for person in before["pedestrians"]:
        pid = person["pedestrian_id"]
        actual = after_by_id[pid]
        for key in ("route", "origin", "destination", "walking_speed_mps"):
            if person[key] != actual[key]:
                violations.append(f"P{pid}: movement changed {key}")
        walking, waiting = 0.0, 0.0
        index, position = person["current_edge_index"], person["position_m"]
        checked = person["signal_wait_edge_index"]
        countdown = person["signal_wait_remaining_sec"]
        ids = list(person["waiting_signal_ids"])
        state = person["state"]
        paused = state == "waiting" and countdown == 0
        if state != "arrived" and not paused and dt > 0:
            segments, _, _, speed, _ = network.remaining(person)
            remaining = dt
            first_index = index
            for offset, segment in enumerate(segments):
                if remaining <= 0:
                    break
                index = first_index + offset
                # Match once-per-departure sampling, including zero-second waits.
                if position == 0 and checked != index:
                    checked = index
                    countdown = segment.waiting_s
                    if countdown > 0:
                        state = "waiting"
                        ids = sorted(a.actuator_id for a in network.graph.actuators.values()
                                     if a.actuator_type == "traffic_signal"
                                     and a.anchor_node == segment.start
                                     and segment.edge.edge_id in a.controlled_edges)
                spent = min(remaining, countdown)
                countdown -= spent
                waiting += spent
                remaining -= spent
                if countdown > 0:
                    break
                if state == "waiting":
                    state, ids = "walking", []
                if remaining <= 0:
                    break
                spent = min(remaining, segment.walking_s)
                walking += spent
                remaining -= spent
                if spent < segment.walking_s:
                    position += speed * spent
                    break
                position = 0.0
                index += 1
                if index == len(person["route"]) - 1:
                    state = "arrived"
                    break
        expected = {"current_edge_index": index, "position_m": position,
                    "signal_wait_edge_index": checked, "signal_wait_remaining_sec": countdown,
                    "state": state, "waiting_signal_ids": ids,
                    "total_travel_time_sec": person["total_travel_time_sec"] + walking,
                    "total_wait_time_sec": person["total_wait_time_sec"] + waiting}
        for key, value in expected.items():
            if isinstance(value, (int, float)):
                matches = math.isclose(number(actual[key], f"P{pid}.{key}"), value,
                                       rel_tol=0.0, abs_tol=tolerance)
            else:
                matches = actual[key] == value
            if not matches:
                violations.append(f"P{pid}: {key} inconsistent with signal waiting and movement")
    return violations
