"""Load and validate the static campus graph JSON files."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .entities import GraphEdge, GraphNode, StaticActuator
from .graph import CampusGraph


JsonObject = Mapping[str, Any]


def _json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Dataset file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in dataset file {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Dataset file must contain a JSON object: {path}")
    return value


def _records(path: Path, collection_name: str) -> list[dict[str, Any]]:
    document = _json_object(path)
    records = document.get(collection_name)
    if not isinstance(records, list):
        raise ValueError(f"{path}: '{collection_name}' must be an array")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError(f"{path}: every '{collection_name}' item must be an object")
    return records


def _string(
    record: JsonObject,
    field_name: str,
    record_id: str,
    *,
    allow_blank: bool = False,
) -> str:
    value = record.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{record_id}: '{field_name}' must be a string")
    value = value.strip()
    if not value and not allow_blank:
        raise ValueError(f"{record_id}: '{field_name}' cannot be blank")
    return value


def _number(record: JsonObject, field_name: str, record_id: str) -> float:
    value = record.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{record_id}: '{field_name}' must be a number")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{record_id}: '{field_name}' must be finite")
    return numeric_value


def _boolean(record: JsonObject, field_name: str, record_id: str) -> bool:
    value = record.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"{record_id}: '{field_name}' must be true or false")
    return value


def _object(record: JsonObject, field_name: str, record_id: str) -> JsonObject:
    value = record.get(field_name)
    if not isinstance(value, dict):
        raise ValueError(f"{record_id}: '{field_name}' must be an object")
    return value


def _node(record: JsonObject) -> GraphNode:
    node_id = _string(record, "node_id", "node")
    return GraphNode(
        node_id=node_id,
        location_name=_string(
            record, "location_name", node_id, allow_blank=True
        ),
        location_type=_string(record, "location_type", node_id),
        x_m=_number(record, "x_m", node_id),
        y_m=_number(record, "y_m", node_id),
        accessible=_boolean(record, "accessible", node_id),
        description=_string(record, "description", node_id, allow_blank=True),
    )


def _edge(record: JsonObject) -> GraphEdge:
    edge_id = _string(record, "edge_id", "edge")
    edge = GraphEdge(
        edge_id=edge_id,
        from_node=_string(record, "from_node", edge_id),
        to_node=_string(record, "to_node", edge_id),
        distance_m=_number(record, "distance_m", edge_id),
        walk_time_min=_number(record, "walk_time_min", edge_id),
        surface_type=_string(record, "surface_type", edge_id),
        accessible=_boolean(record, "accessible", edge_id),
    )
    if edge.distance_m <= 0:
        raise ValueError(f"{edge_id}: 'distance_m' must be greater than zero")
    return edge


def _edge_relationships(
    record: JsonObject, actuator_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    relationships = record.get("edge_relationships", [])
    if not isinstance(relationships, list):
        raise ValueError(f"{actuator_id}: 'edge_relationships' must be an array")

    controlled_edges: list[str] = []
    recommended_edges: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, relationship_record in enumerate(relationships):
        item_id = f"{actuator_id}.edge_relationships[{index}]"
        if not isinstance(relationship_record, dict):
            raise ValueError(f"{item_id}: relationship must be an object")
        edge_id = _string(relationship_record, "edge_id", item_id)
        relationship = _string(relationship_record, "relationship", item_id)
        key = (edge_id, relationship)
        if key in seen:
            raise ValueError(f"{item_id}: duplicate actuator-edge relationship")
        seen.add(key)
        if relationship == "controls":
            controlled_edges.append(edge_id)
        elif relationship == "recommends":
            recommended_edges.append(edge_id)
        else:
            raise ValueError(
                f"{item_id}: 'relationship' must be 'controls' or 'recommends'"
            )
    return tuple(controlled_edges), tuple(recommended_edges)


def _actuator(record: JsonObject) -> StaticActuator:
    actuator_id = _string(record, "actuator_id", "actuator")
    actuator_type = _string(record, "actuator_type", actuator_id)
    allowed_parameters = {
        "misting_point": {"max_heat_reduction_c"},
        "traffic_signal": {
            "default_wait_time_s",
            "min_wait_time_s",
            "max_wait_time_s",
        },
        "directional_led": set(),
    }
    if actuator_type not in allowed_parameters:
        raise ValueError(
            f"{actuator_id}: unsupported actuator type '{actuator_type}'"
        )

    position = _object(record, "position", actuator_id)
    parameters = _object(record, "parameters", actuator_id)
    parameter_names = set(parameters)
    expected_parameters = allowed_parameters[actuator_type]
    if parameter_names != expected_parameters:
        missing = sorted(expected_parameters - parameter_names)
        unexpected = sorted(parameter_names - expected_parameters)
        raise ValueError(
            f"{actuator_id}: invalid parameters; missing={missing}, "
            f"unexpected={unexpected}"
        )

    controlled_edges, recommended_edges = _edge_relationships(
        record, actuator_id
    )
    if actuator_type == "misting_point":
        max_heat_reduction_c = _number(
            parameters, "max_heat_reduction_c", actuator_id
        )
        if max_heat_reduction_c <= 0:
            raise ValueError(
                f"{actuator_id}: 'max_heat_reduction_c' must be greater than zero"
            )
        if controlled_edges or recommended_edges:
            raise ValueError(
                f"{actuator_id}: misting points cannot reference graph edges"
            )
        default_wait_time_s = min_wait_time_s = max_wait_time_s = None
    elif actuator_type == "traffic_signal":
        max_heat_reduction_c = None
        default_wait_time_s = _number(
            parameters, "default_wait_time_s", actuator_id
        )
        min_wait_time_s = _number(
            parameters, "min_wait_time_s", actuator_id
        )
        max_wait_time_s = _number(
            parameters, "max_wait_time_s", actuator_id
        )
        if not 0 <= min_wait_time_s <= default_wait_time_s <= max_wait_time_s:
            raise ValueError(
                f"{actuator_id}: wait times must satisfy 0 <= min <= default <= max"
            )
        if not controlled_edges or recommended_edges:
            raise ValueError(
                f"{actuator_id}: traffic signals require only 'controls' relationships"
            )
    else:
        max_heat_reduction_c = None
        default_wait_time_s = min_wait_time_s = max_wait_time_s = None
        if not recommended_edges or controlled_edges:
            raise ValueError(
                f"{actuator_id}: directional LEDs require only 'recommends' relationships"
            )

    return StaticActuator(
        actuator_id=actuator_id,
        actuator_type=actuator_type,
        anchor_node=_string(position, "anchor_node", actuator_id),
        x_m=_number(position, "x_m", actuator_id),
        y_m=_number(position, "y_m", actuator_id),
        controlled_edges=controlled_edges,
        recommended_edges=recommended_edges,
        max_heat_reduction_c=max_heat_reduction_c,
        default_wait_time_s=default_wait_time_s,
        min_wait_time_s=min_wait_time_s,
        max_wait_time_s=max_wait_time_s,
        description=_string(
            record, "description", actuator_id, allow_blank=True
        ),
        properties={
            "status": (
                "off"
                if actuator_type in {"misting_point", "directional_led"}
                else None
            ),
            "wait_time_s": (
                default_wait_time_s if actuator_type == "traffic_signal" else None
            ),
            "recommended_edge": None,
        },
    )


def _dataset_file(dataset_path: Path, files: JsonObject, name: str) -> Path:
    filename = _string(files, name, "dataset manifest")
    relative_path = Path(filename)
    if relative_path.is_absolute() or len(relative_path.parts) != 1:
        raise ValueError(
            f"dataset manifest: '{name}' must name a file in the dataset directory"
        )
    return dataset_path / relative_path


def load_campus_graph(directory: str | Path) -> CampusGraph:
    """Load and validate a campus graph from its JSON dataset directory."""
    dataset_path = Path(directory)
    manifest = _json_object(dataset_path / "dataset.json")
    graph_metadata = _object(manifest, "graph", "dataset manifest")
    if graph_metadata.get("directed") is not False:
        raise ValueError("dataset.json: this project requires an undirected graph")
    files = _object(manifest, "files", "dataset manifest")

    node_list = [
        _node(record)
        for record in _records(
            _dataset_file(dataset_path, files, "nodes"), "nodes"
        )
    ]
    edges = tuple(
        _edge(record)
        for record in _records(
            _dataset_file(dataset_path, files, "edges"), "edges"
        )
    )
    actuator_list = [
        _actuator(record)
        for record in _records(
            _dataset_file(dataset_path, files, "actuators"), "actuators"
        )
    ]

    nodes = {node.node_id: node for node in node_list}
    if len(nodes) != len(node_list):
        raise ValueError("Node identifiers must be unique")
    if len({edge.edge_id for edge in edges}) != len(edges):
        raise ValueError("Edge identifiers must be unique")
    actuators = {actuator.actuator_id: actuator for actuator in actuator_list}
    if len(actuators) != len(actuator_list):
        raise ValueError("Actuator identifiers must be unique")

    for edge in edges:
        if edge.from_node not in nodes or edge.to_node not in nodes:
            raise ValueError(f"{edge.edge_id}: edge endpoint does not exist")
        if edge.from_node == edge.to_node:
            raise ValueError(f"{edge.edge_id}: self-loop edges are not allowed")

    edge_ids = {edge.edge_id for edge in edges}
    for actuator in actuators.values():
        if actuator.anchor_node not in nodes:
            raise ValueError(
                f"{actuator.actuator_id}: actuator anchor node does not exist"
            )
        referenced_edges = set(
            actuator.controlled_edges + actuator.recommended_edges
        )
        missing_edges = sorted(referenced_edges - edge_ids)
        if missing_edges:
            raise ValueError(
                f"{actuator.actuator_id}: referenced edges do not exist: "
                f"{missing_edges}"
            )
    return CampusGraph(nodes=nodes, edges=edges, actuators=actuators)
