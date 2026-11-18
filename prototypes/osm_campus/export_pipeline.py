"""Export routingc's real walkway graph to the shared pipeline dataset.

Run after upc_graph.py, from this folder: python export_pipeline.py
Geometry vertices become ordinary graph nodes. This preserves curved walkway
lengths and distinguishes parallel OSM edges for node-based pedestrian routes.
Shade and actuator settings below are explicit simulation assumptions, not
measurements or claims about installed USC infrastructure.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import osmnx as ox
from shapely.geometry import LineString

HERE = Path(__file__).resolve().parent
DESTINATION = HERE.parents[1] / "dataset" / "usc_osm_trimmed"


def export() -> dict:
    graph = ox.load_graphml(HERE / "upc_walk_trimmed.graphml")
    if graph.is_directed():
        raise ValueError("Export expects routingc's undirected trimmed graph")
    buildings = json.loads((HERE / "buildings_trimmed.json").read_text())
    origin_x = min(data["x"] for _, data in graph.nodes(data=True))
    origin_y = min(data["y"] for _, data in graph.nodes(data=True))
    node_ids = {node: f"U{node:06d}" for node in graph.nodes}
    names = {int(node): name for name, doors in buildings.items() for node in doors}
    nodes, edges, provenance = [], [], []

    def add_node(identifier, x, y, name="", description="", kind="intersection"):
        nodes.append({
            "node_id": identifier, "location_name": name,
            "location_type": kind, "x_m": x - origin_x, "y_m": y - origin_y,
            "accessible": True, "description": description,
        })

    for node, data in sorted(graph.nodes(data=True)):
        add_node(
            node_ids[node], data["x"], data["y"], names.get(node, ""),
            f"OSM prototype node {node}; entrance source: {data.get('source', 'not an entrance')}",
            "entrance" if node in names else "intersection",
        )

    for index, (u, v, key, data) in enumerate(
        sorted(graph.edges(keys=True, data=True), key=lambda e: (min(e[0], e[1]), max(e[0], e[1]), e[2])), 1
    ):
        if u == v:
            raise ValueError("The trimmed source unexpectedly contains a self-loop")
        start = (graph.nodes[u]["x"], graph.nodes[u]["y"])
        end = (graph.nodes[v]["x"], graph.nodes[v]["y"])
        line = data.get("geometry", LineString([start, end]))
        coords = list(line.coords)
        if math.dist(coords[0], end) < math.dist(coords[0], start):
            coords.reverse()
        coords[0], coords[-1] = start, end
        coords = [point for i, point in enumerate(coords) if i == 0 or math.dist(point, coords[i - 1]) > 1e-9]
        # Always introduce an edge-specific intermediate node, including on
        # straight parallel edges. No source edge is silently discarded.
        if len(coords) == 2:
            coords.insert(1, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
        lengths = [math.dist(a, b) for a, b in zip(coords, coords[1:])]
        total = sum(lengths)
        source_length = float(data["length"])
        if total <= 0 or not math.isfinite(source_length) or source_length <= 0:
            raise ValueError(f"Invalid source edge length: {(u, v, key)}")
        chain = [node_ids[u]]
        for i, point in enumerate(coords[1:-1], 1):
            identifier = f"V{index:04d}_{i:03d}"
            add_node(identifier, *point, description=f"Shape vertex of OSM edge {(u, v, key)}")
            chain.append(identifier)
        chain.append(node_ids[v])
        segment_ids = []
        for i, (a, b, length) in enumerate(zip(chain, chain[1:], lengths), 1):
            identifier = f"OSM{index:04d}_{i:03d}"
            distance = source_length * length / total
            edges.append({
                "edge_id": identifier, "from_node": a, "to_node": b,
                "distance_m": distance, "walk_time_min": distance / 1.4 / 60,
                "surface_type": "osm_walkway", "accessible": True,
            })
            segment_ids.append(identifier)
        provenance.append({"u": u, "v": v, "key": key, "length_m": source_length, "pipeline_edges": segment_ids})

    pairs = Counter(frozenset((e["from_node"], e["to_node"])) for e in edges)
    assert all(count == 1 for count in pairs.values())
    by_id = {node["node_id"]: node for node in nodes}
    building_nodes = {name: [node_ids[int(n)] for n in doors] for name, doors in buildings.items()}
    actuators = []
    for i, name in enumerate(["Leavey Library", "Seeley G. Mudd Building"], 1):
        anchor = building_nodes[name][0]
        n = by_id[anchor]
        actuators.append({
            "actuator_id": f"SIM_M{i}", "actuator_type": "misting_point",
            "position": {"anchor_node": anchor, "x_m": n["x_m"], "y_m": n["y_m"]},
            "parameters": {"max_heat_reduction_f": 5.4}, "edge_relationships": [],
            "description": f"Hypothetical experiment mister at {name}; not a surveyed installation",
        })
    degree = Counter(node for e in edges for node in (e["from_node"], e["to_node"]))
    for i, anchor in enumerate(sorted(node_ids.values(), key=lambda n: (-degree[n], n))[:2], 1):
        n = by_id[anchor]
        actuators.append({
            "actuator_id": f"SIM_S{i}", "actuator_type": "traffic_signal",
            "position": {"anchor_node": anchor, "x_m": n["x_m"], "y_m": n["y_m"]},
            "parameters": {"default_wait_time_s": 30, "min_wait_time_s": 10, "max_wait_time_s": 90},
            "edge_relationships": [{"edge_id": e["edge_id"], "relationship": "controls"} for e in edges if anchor in (e["from_node"], e["to_node"])],
            "description": "Hypothetical experiment signal at a high-degree intersection; not a surveyed installation",
        })
    slots = [f"{m // 60:02d}:{m % 60:02d}" for m in range(480, 1081, 30)]
    manifest = {
        "dataset_id": "usc_osm_trimmed",
        "description": "Real USC OSM walkway geometry with assumed uniform shade and hypothetical actuators",
        "graph": {"directed": False, "coordinate_system": {"type": "local_cartesian", "unit": "meter", "source_crs": str(graph.graph["crs"]), "origin": [origin_x, origin_y]}},
        "files": {"nodes": "nodes.json", "edges": "edges.json", "actuators": "actuators.json", "shade_schedule": "shade_schedule.json", "buildings": "buildings.json"},
        "source": {"branch": "routingc", "map": "OpenStreetMap contributors (ODbL)", "source_nodes": len(graph.nodes), "source_edges": len(graph.edges), "buildings": len(buildings)},
        "assumptions": ["Shade is uniformly assumed at 5%; not measured.", "Two misters and two signals are hypothetical simulation infrastructure.", "Source accessibility has not been surveyed; exported walkways are traversable for this generic walking scenario.", "Most building entrances were inferred by the source prototype."],
    }
    files = {
        "dataset.json": manifest, "nodes.json": {"nodes": nodes}, "edges.json": {"edges": edges},
        "actuators.json": {"actuators": actuators}, "buildings.json": building_nodes,
        "shade_schedule.json": {"time_slots": slots, "profiles": {"assumed_open": {slot: 5.0 for slot in slots}}, "edges": {e["edge_id"]: "assumed_open" for e in edges}},
        "source_edges.json": {"source_edges": provenance},
    }
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (DESTINATION / name).write_text(json.dumps(value, indent=2) + "\n")
    print(f"Exported {len(nodes)} nodes, {len(edges)} segments, {len(buildings)} buildings to {DESTINATION}")
    return manifest


if __name__ == "__main__":
    export()
