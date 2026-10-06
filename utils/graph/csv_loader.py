"""Load and validate the static campus graph CSV files."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Mapping

from .entities import GraphEdge, GraphNode
from .graph import CampusGraph


def _number(row: Mapping[str, str], field_name: str, record_id: str) -> float:
    try:
        value = float(row[field_name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{record_id}: '{field_name}' must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{record_id}: '{field_name}' must be finite")
    return value


def _boolean(row: Mapping[str, str], field_name: str, record_id: str) -> bool:
    value = row.get(field_name, "").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{record_id}: '{field_name}' must be true or false")
    return value == "true"


def _rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"Dataset file has no header: {path}")
            return list(reader)
    except FileNotFoundError as exc:
        raise ValueError(f"Dataset file not found: {path}") from exc


def _node(row: Mapping[str, str]) -> GraphNode:
    node_id = row.get("node_id", "").strip()
    if not node_id:
        raise ValueError("Every node must have a node_id")
    location_type = row.get("location_type", "").strip()
    if not location_type:
        raise ValueError(f"{node_id}: 'location_type' cannot be blank")
    return GraphNode(
        node_id=node_id,
        location_name=row.get("location_name", "").strip(),
        location_type=location_type,
        x_m=_number(row, "x_m", node_id),
        y_m=_number(row, "y_m", node_id),
        accessible=_boolean(row, "accessible", node_id),
        description=row.get("description", "").strip(),
    )


def _edge(row: Mapping[str, str]) -> GraphEdge:
    edge_id = row.get("edge_id", "").strip()
    if not edge_id:
        raise ValueError("Every edge must have an edge_id")
    edge = GraphEdge(
        edge_id=edge_id,
        from_node=row.get("from_node", "").strip(),
        to_node=row.get("to_node", "").strip(),
        distance_m=_number(row, "distance_m", edge_id),
        walk_time_min=_number(row, "walk_time_min", edge_id),
        surface_type=row.get("surface_type", "").strip(),
        accessible=_boolean(row, "accessible", edge_id),
    )
    if edge.distance_m <= 0:
        raise ValueError(f"{edge_id}: 'distance_m' must be greater than zero")
    return edge


def load_campus_graph(directory: str | Path) -> CampusGraph:
    """Load nodes.csv and edges.csv from a dataset directory."""
    dataset_path = Path(directory)
    node_list = [_node(row) for row in _rows(dataset_path / "nodes.csv")]
    edges = tuple(_edge(row) for row in _rows(dataset_path / "edges.csv"))

    nodes = {node.node_id: node for node in node_list}
    if len(nodes) != len(node_list):
        raise ValueError("Node identifiers must be unique")
    if len({edge.edge_id for edge in edges}) != len(edges):
        raise ValueError("Edge identifiers must be unique")
    for edge in edges:
        if edge.from_node not in nodes or edge.to_node not in nodes:
            raise ValueError(f"{edge.edge_id}: edge endpoint does not exist")
        if edge.from_node == edge.to_node:
            raise ValueError(f"{edge.edge_id}: self-loop edges are not allowed")
    return CampusGraph(nodes=nodes, edges=edges)

