"""Apply small misting placement overlays without duplicating a campus map."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

from .entities import StaticActuator
from .graph import CampusGraph

MISTING_COUNTS = (0, 4, 6, 10)


def apply_misting_scenario(
    graph: CampusGraph, directory: Path, count: int, radius_m: float,
) -> tuple[CampusGraph, dict]:
    """Replace only misters, selecting the first count placements in the overlay.

    The environment configuration remains the single source of the radius.
    Positions are resolved from map anchors so they cannot drift from the graph.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count not in MISTING_COUNTS:
        raise ValueError(f"Misting count must be one of {MISTING_COUNTS}")
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("Misting radius must be finite and positive")
    path = Path(directory) / "misting_scenarios.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        manifest = json.loads((Path(directory) / "dataset.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load misting scenarios for {directory}: {exc}") from exc
    if not isinstance(config, dict) or config.get("dataset_id") != manifest.get("dataset_id"):
        raise ValueError("Misting scenarios must match the selected dataset_id")
    placements = config.get("placements")
    if not isinstance(placements, list) or len(placements) < max(MISTING_COUNTS):
        raise ValueError("Misting scenarios require at least 10 ordered placements")
    strength = config.get("max_heat_reduction_f")
    if (isinstance(strength, bool) or not isinstance(strength, (int, float))
            or not math.isfinite(strength) or strength <= 0):
        raise ValueError("Scenario max_heat_reduction_f must be finite and positive")

    actuators = {key: actuator for key, actuator in graph.actuators.items()
                 if actuator.actuator_type != "misting_point"}
    connected_nodes = {node for edge in graph.edges for node in (edge.from_node, edge.to_node)}
    selected = []
    for placement in placements[:count]:
        if not isinstance(placement, dict):
            raise ValueError("Each misting placement must be an object")
        actuator_id, anchor = placement.get("actuator_id"), placement.get("anchor_node")
        if not isinstance(actuator_id, str) or not actuator_id.strip() or actuator_id in actuators:
            raise ValueError(f"Invalid or duplicate scenario actuator ID: {actuator_id}")
        if not isinstance(anchor, str) or anchor not in graph.nodes or anchor not in connected_nodes:
            raise ValueError(f"{actuator_id}: anchor must exist and connect to a walkway: {anchor}")
        node = graph.nodes[anchor]
        for other in selected:
            if math.hypot(node.x_m - other["x_m"], node.y_m - other["y_m"]) <= 2 * radius_m:
                raise ValueError(f"Misting zones overlap or touch: {actuator_id} and {other['actuator_id']}")
        description = placement.get("description", "Hypothetical misting station")
        if not isinstance(description, str):
            raise ValueError(f"{actuator_id}: description must be a string")
        actuators[actuator_id] = StaticActuator(
            actuator_id, "misting_point", anchor, node.x_m, node.y_m,
            max_heat_reduction_f=float(strength), description=description,
            properties={"status": "off", "wait_time_s": None, "recommended_edge": None})
        selected.append({"actuator_id": actuator_id, "anchor_node": anchor,
                         "x_m": node.x_m, "y_m": node.y_m, "description": description,
                         "max_heat_reduction_f": float(strength)})
    metadata = {"dataset_id": config["dataset_id"], "misting_count": count,
                "mist_range_m": radius_m, "configuration_file": str(path.resolve()),
                "placements": selected,
                "preserved_actuator_ids": [key for key, a in actuators.items()
                                           if a.actuator_type != "misting_point"]}
    return replace(graph, actuators=actuators), metadata
