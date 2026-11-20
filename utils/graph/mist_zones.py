"""Cut walkways where they cross misting circles.

The simulation stores one set of conditions per edge. To cool only the part of
a walkway that lies inside a mister's circle, each straight edge that crosses
a circle boundary is split at the crossing points before the simulation
starts. Afterwards every edge is either fully inside or fully outside each
circle, so Environment's covered-share cooling gives full cooling to the
inside pieces and none to the rest.

Piece IDs are ``<original edge ID>~<n>`` in walking order from the original
``from_node``; new boundary nodes are ``<original edge ID>~n<k>``. Distances
and walking times are split in proportion to the straight-line position.
Signal ``controlled_edges`` and LED ``recommended_edges`` that referred to a
split edge list all of its pieces instead.
"""

from __future__ import annotations

import math
from dataclasses import replace

from .entities import GraphEdge, GraphNode
from .graph import CampusGraph

PIECE_SEPARATOR = "~"
# Crossings closer than this (as a share of the edge) to an end are ignored.
_END_TOLERANCE = 1e-9


def parent_edge_id(edge_id: str) -> str:
    """Original dataset edge ID for a piece, or the ID itself if never split."""
    return edge_id.split(PIECE_SEPARATOR, 1)[0]


def expand_split_ids(edge_ids: list[str], graph: CampusGraph) -> list[str]:
    """Replace split original IDs with their pieces, keeping walking order."""
    existing = {edge.edge_id for edge in graph.edges}
    pieces: dict[str, list[str]] = {}
    for edge in graph.edges:
        if PIECE_SEPARATOR in edge.edge_id:
            pieces.setdefault(parent_edge_id(edge.edge_id), []).append(edge.edge_id)
    expanded = []
    for identifier in edge_ids:
        if identifier in existing:
            expanded.append(identifier)
        elif identifier in pieces:
            expanded.extend(sorted(pieces[identifier],
                                   key=lambda piece: int(piece.rsplit(PIECE_SEPARATOR, 1)[1])))
        else:
            expanded.append(identifier)
    return expanded


def _crossings(cx: float, cy: float, radius: float,
               ax: float, ay: float, bx: float, by: float) -> list[float]:
    """Positions (0-1 along A->B) where the segment strictly crosses the circle."""
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if radius <= 0 or length_squared == 0:
        return []
    ox, oy = ax - cx, ay - cy
    b = 2.0 * (ox * dx + oy * dy)
    c = ox * ox + oy * oy - radius * radius
    discriminant = b * b - 4.0 * length_squared * c
    if discriminant <= 0:          # misses or only touches: no inside piece
        return []
    root = math.sqrt(discriminant)
    roots = ((-b - root) / (2.0 * length_squared), (-b + root) / (2.0 * length_squared))
    return [t for t in roots if _END_TOLERANCE < t < 1.0 - _END_TOLERANCE]


def split_edges_at_mist_zones(graph: CampusGraph, radius_m: float) -> tuple[CampusGraph, dict]:
    """Split every edge at the circles of all misting points on the graph.

    Splitting uses every mister present, whatever its status, so the graph
    stays fixed while misters switch on and off during a run. Returns the new
    graph and a summary for run diagnostics.
    """
    if isinstance(radius_m, bool) or not isinstance(radius_m, (int, float)) \
            or not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("Misting radius must be finite and positive")
    misters = [a for a in graph.actuators.values() if a.actuator_type == "misting_point"]
    nodes = dict(graph.nodes)
    edges: list[GraphEdge] = []
    pieces_by_parent: dict[str, tuple[str, ...]] = {}
    for edge in graph.edges:
        if PIECE_SEPARATOR in edge.edge_id:
            raise ValueError(f"{edge.edge_id}: graph has already been split at mist zones")
        a, b = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
        cuts = sorted({t for mister in misters
                       for t in _crossings(mister.x_m, mister.y_m, radius_m,
                                           a.x_m, a.y_m, b.x_m, b.y_m)})
        if not cuts:
            edges.append(edge)
            continue
        positions = [0.0, *cuts, 1.0]
        chain = [edge.from_node]
        for k, t in enumerate(cuts, start=1):
            node_id = f"{edge.edge_id}{PIECE_SEPARATOR}n{k}"
            if node_id in nodes:
                raise ValueError(f"Mist-zone node ID already exists: {node_id}")
            nodes[node_id] = GraphNode(
                node_id, "", "intersection",
                a.x_m + t * (b.x_m - a.x_m), a.y_m + t * (b.y_m - a.y_m),
                edge.accessible, f"Mist zone boundary on walkway {edge.edge_id}")
            chain.append(node_id)
        chain.append(edge.to_node)
        piece_ids = []
        for k, (start, end) in enumerate(zip(positions, positions[1:]), start=1):
            share = end - start
            piece_id = f"{edge.edge_id}{PIECE_SEPARATOR}{k}"
            piece_ids.append(piece_id)
            edges.append(GraphEdge(
                piece_id, chain[k - 1], chain[k], edge.distance_m * share,
                edge.walk_time_min * share, edge.surface_type, edge.accessible,
                properties=dict(edge.properties)))
        pieces_by_parent[edge.edge_id] = tuple(piece_ids)

    def remap(identifiers: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(piece for identifier in identifiers
                     for piece in pieces_by_parent.get(identifier, (identifier,)))

    actuators = {
        key: replace(actuator, controlled_edges=remap(actuator.controlled_edges),
                     recommended_edges=remap(actuator.recommended_edges))
        for key, actuator in graph.actuators.items()
    }
    summary = {
        "mist_range_m": float(radius_m),
        "misters": sorted(a.actuator_id for a in misters),
        "split_walkways": len(pieces_by_parent),
        "added_nodes": len(nodes) - len(graph.nodes),
        "edge_count_before": len(graph.edges),
        "edge_count_after": len(edges),
    }
    return replace(graph, nodes=nodes, edges=tuple(edges), actuators=actuators), summary
