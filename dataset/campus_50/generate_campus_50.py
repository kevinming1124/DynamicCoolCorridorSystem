"""Generate the 50-node, 100-edge campus dataset used by the environment model.

Run from the repository root:

    python dataset/campus_50/generate_campus_50.py

The script is deterministic: running it again produces identical files.
It uses only the Python standard library and writes every file into the
folder that contains this script.

Files written:
    dataset.json          manifest (same format as tiny_campus_block)
    nodes.json            50 nodes on a 5 x 10 grid, local meters
    edges.json            100 bidirectional walking edges
    actuators.json        misting points and pedestrian traffic signals
    shade_schedule.json   shade percent for every edge at every 30-minute step
"""

from __future__ import annotations

import json
import math
import random
from collections import deque
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent
SEED = 42

# ---------------------------------------------------------------------------
# Layout settings (edit here, then rerun the script)
# ---------------------------------------------------------------------------
ROWS = 5                 # south (row 0) to north (row 4)
COLS = 10                # west (col 0) to east (col 9)
SPACING_X_M = 60.0       # east-west distance between neighboring nodes
SPACING_Y_M = 50.0       # north-south distance between neighboring nodes
DIAGONAL_COUNT = 15      # 85 grid edges + 15 diagonals = 100 edges
WALK_SPEED_M_S = 1.4     # same walking speed as tiny_campus_block

MISTER_MAX_REDUCTION_F = 5.4
SIGNAL_COUNT = 6
SIGNAL_WAIT_S = {"default": 30, "min": 10, "max": 90}

# Simulated day: 08:00 to 18:00 in 30-minute steps (21 time slots)
TIME_SLOTS = [f"{m // 60:02d}:{m % 60:02d}" for m in range(8 * 60, 18 * 60 + 1, 30)]

# Named places, placed at (row, col). Every other node is an intersection.
# Names come from the USC University Park campus. Positions roughly follow the
# real layout (USC Village in the north, the Expo Line station in the south,
# Doheny and Tommy Trojan near the middle) but are not to scale.
NAMED_PLACES = {
    (0, 1): ("Parkside", "dormitory", "Parkside residential complex"),
    (0, 3): ("Everybody's Kitchen (EVK)", "dining_hall", "Main residential dining hall"),
    (0, 5): ("Expo Park/USC Metro Station", "transit_stop", "Metro E Line station on the south edge of campus"),
    (1, 1): ("Lyon Center", "recreation_center", "Recreation and fitness center"),
    (1, 5): ("Alumni Park", "open_space", "Large open lawn in the center of campus"),
    (1, 7): ("Seeley G. Mudd Building", "teaching_building", "Classroom and lab building"),
    (2, 2): ("Ronald Tutor Hall", "teaching_building", "Viterbi engineering classrooms and labs"),
    (2, 4): ("Doheny Memorial Library", "library", "Main campus library"),
    (2, 5): ("Tommy Trojan", "landmark", "Central campus landmark and meeting point"),
    (2, 6): ("Ronald Tutor Campus Center", "student_center", "Student services, food court, and lounges"),
    (3, 4): ("Leavey Library", "library", "24-hour library and study space"),
    (3, 5): ("McCarthy Quad", "open_space", "Open lawn between Doheny and Leavey"),
    (3, 7): ("Taper Hall", "teaching_building", "Humanities classrooms"),
    (4, 3): ("USC Village", "dormitory", "Residential village on the north side of campus"),
    (4, 7): ("Webb Tower", "dormitory", "Graduate residence tower"),
}

# Misting points are placed at these named places (open, busy, sun-exposed spots).
MISTER_PLACES = ["McCarthy Quad", "Alumni Park", "Ronald Tutor Campus Center", "Expo Park/USC Metro Station"]

# ---------------------------------------------------------------------------
# Shade profiles: percent of the path that is shaded at each time slot.
#   open           no buildings or trees nearby
#   tree_lined     trees along the path, shaded all day
#   building_east  building on the east side, shades the path in the morning
#   building_west  building on the west side, shades the path in the afternoon
# ---------------------------------------------------------------------------
def _profile(rule) -> dict[str, float]:
    return {slot: float(rule(slot)) for slot in TIME_SLOTS}


def _hour(slot: str) -> float:
    hours, minutes = slot.split(":")
    return int(hours) + int(minutes) / 60


SHADE_PROFILES = {
    "open": _profile(lambda s: 5),
    "tree_lined": _profile(lambda s: 60),
    "building_east": _profile(
        lambda s: 70 if _hour(s) <= 10.5 else 40 if _hour(s) <= 11.5 else 15
    ),
    "building_west": _profile(
        lambda s: 15 if _hour(s) <= 13.0 else 40 if _hour(s) <= 14.0 else 70
    ),
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def node_id(row: int, col: int) -> str:
    return f"N{row * COLS + col + 1:02d}"


def build_nodes() -> list[dict]:
    nodes = []
    for row in range(ROWS):
        for col in range(COLS):
            name, kind, description = NAMED_PLACES.get(
                (row, col), ("", "intersection", "Unnamed pedestrian path intersection")
            )
            nodes.append(
                {
                    "node_id": node_id(row, col),
                    "location_name": name,
                    "location_type": kind,
                    "x_m": col * SPACING_X_M,
                    "y_m": row * SPACING_Y_M,
                    "accessible": True,
                    "description": description,
                }
            )
    return nodes


def build_edges(rng: random.Random) -> tuple[list[dict], dict[str, str]]:
    """Return edge records and each edge's orientation (ew, ns, or diagonal)."""
    pairs: list[tuple[tuple[int, int], tuple[int, int], str]] = []

    for row in range(ROWS):                       # east-west edges: 5 x 9 = 45
        for col in range(COLS - 1):
            pairs.append(((row, col), (row, col + 1), "ew"))
    for row in range(ROWS - 1):                   # north-south edges: 4 x 10 = 40
        for col in range(COLS):
            pairs.append(((row, col), (row + 1, col), "ns"))

    cells = [(row, col) for row in range(ROWS - 1) for col in range(COLS - 1)]
    for row, col in sorted(rng.sample(cells, DIAGONAL_COUNT)):  # one diagonal per cell
        if rng.random() < 0.5:
            pairs.append(((row, col), (row + 1, col + 1), "diagonal"))
        else:
            pairs.append(((row, col + 1), (row + 1, col), "diagonal"))

    edges, orientation = [], {}
    for index, ((r1, c1), (r2, c2), kind) in enumerate(pairs, start=1):
        edge_id = f"E{index:03d}"
        distance = math.hypot((c2 - c1) * SPACING_X_M, (r2 - r1) * SPACING_Y_M)
        edges.append(
            {
                "edge_id": edge_id,
                "from_node": node_id(r1, c1),
                "to_node": node_id(r2, c2),
                "distance_m": round(distance, 2),
                "walk_time_min": round(distance / WALK_SPEED_M_S / 60, 2),
                "surface_type": "paved",
                "accessible": True,
            }
        )
        orientation[edge_id] = kind
    return edges, orientation


def assign_shade_profiles(edges: list[dict], orientation: dict[str, str]) -> dict[str, str]:
    """Pick a shade profile for every edge using a simple, explainable rule.

    North-south paths run beside buildings: even columns have the building on
    the east (morning shade), odd columns on the west (afternoon shade).
    East-west paths on rows 1 and 3 are tree-lined; the rest are open.
    Diagonal shortcuts cut across lawns and plazas, so they are open.
    """
    profiles = {}
    for edge in edges:
        edge_id = edge["edge_id"]
        index = int(edge["from_node"][1:]) - 1
        row, col = divmod(index, COLS)
        kind = orientation[edge_id]
        if kind == "ns":
            profiles[edge_id] = "building_east" if col % 2 == 0 else "building_west"
        elif kind == "ew":
            profiles[edge_id] = "tree_lined" if row in (1, 3) else "open"
        else:
            profiles[edge_id] = "open"
    return profiles


def build_actuators(nodes: list[dict], edges: list[dict]) -> list[dict]:
    by_name = {node["location_name"]: node for node in nodes if node["location_name"]}
    actuators = []

    for name in MISTER_PLACES:
        node = by_name[name]
        actuators.append(
            {
                "actuator_id": f"A{len(actuators) + 1:02d}",
                "actuator_type": "misting_point",
                "position": {"anchor_node": node["node_id"], "x_m": node["x_m"], "y_m": node["y_m"]},
                "parameters": {"max_heat_reduction_f": MISTER_MAX_REDUCTION_F},
                "edge_relationships": [],
                "description": f"{name} misting point for localized heat reduction",
            }
        )

    incident: dict[str, list[str]] = {node["node_id"]: [] for node in nodes}
    for edge in edges:
        incident[edge["from_node"]].append(edge["edge_id"])
        incident[edge["to_node"]].append(edge["edge_id"])

    # Signals go at the busiest unnamed intersections (most connected edges).
    intersections = [n for n in nodes if n["location_type"] == "intersection"]
    intersections.sort(key=lambda n: (-len(incident[n["node_id"]]), n["node_id"]))
    for node in intersections[:SIGNAL_COUNT]:
        actuators.append(
            {
                "actuator_id": f"A{len(actuators) + 1:02d}",
                "actuator_type": "traffic_signal",
                "position": {"anchor_node": node["node_id"], "x_m": node["x_m"], "y_m": node["y_m"]},
                "parameters": {
                    "default_wait_time_s": SIGNAL_WAIT_S["default"],
                    "min_wait_time_s": SIGNAL_WAIT_S["min"],
                    "max_wait_time_s": SIGNAL_WAIT_S["max"],
                },
                "edge_relationships": [
                    {"edge_id": edge_id, "relationship": "controls"}
                    for edge_id in sorted(incident[node["node_id"]])
                ],
                "description": f"Adjustable pedestrian signal at intersection {node['node_id']}",
            }
        )
    return actuators


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check(nodes: list[dict], edges: list[dict], shade: dict) -> None:
    assert len(nodes) == 50, f"expected 50 nodes, got {len(nodes)}"
    assert len(edges) == 100, f"expected 100 edges, got {len(edges)}"
    pairs = {frozenset((e["from_node"], e["to_node"])) for e in edges}
    assert len(pairs) == len(edges), "duplicate edges between the same two nodes"

    neighbors: dict[str, set[str]] = {n["node_id"]: set() for n in nodes}
    for edge in edges:
        neighbors[edge["from_node"]].add(edge["to_node"])
        neighbors[edge["to_node"]].add(edge["from_node"])
    seen, queue = {nodes[0]["node_id"]}, deque([nodes[0]["node_id"]])
    while queue:
        for nxt in neighbors[queue.popleft()] - seen:
            seen.add(nxt)
            queue.append(nxt)
    assert len(seen) == len(nodes), "graph is not connected"

    assert set(shade["edges"]) == {e["edge_id"] for e in edges}, "shade missing for some edges"
    for name, profile in shade["profiles"].items():
        assert list(profile) == TIME_SLOTS, f"profile '{name}' is missing time slots"
        assert all(0 <= v <= 100 for v in profile.values()), f"profile '{name}' out of range"


def write_json(name: str, data: dict) -> None:
    path = OUTPUT_DIR / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    nodes = build_nodes()
    edges, orientation = build_edges(rng)
    actuators = build_actuators(nodes, edges)
    shade = {
        "description": "Shade percent (0-100) for every edge at every 30-minute time slot, 08:00-18:00.",
        "time_slots": TIME_SLOTS,
        "profiles": SHADE_PROFILES,
        "edges": assign_shade_profiles(edges, orientation),
    }
    check(nodes, edges, shade)

    write_json(
        "dataset.json",
        {
            "dataset_id": "campus_50",
            "description": "50-node, 100-edge campus graph inspired by USC University Park (not to scale)",
            "graph": {
                "directed": False,
                "coordinate_system": {
                    "type": "local_cartesian",
                    "unit": "meter",
                    "origin": "Southwest corner of the simulated campus",
                },
            },
            "files": {
                "nodes": "nodes.json",
                "edges": "edges.json",
                "actuators": "actuators.json",
                "shade_schedule": "shade_schedule.json",
            },
        },
    )
    write_json("nodes.json", {"nodes": nodes})
    write_json("edges.json", {"edges": edges})
    write_json("actuators.json", {"actuators": actuators})
    write_json("shade_schedule.json", shade)

    counts = {}
    for profile in shade["edges"].values():
        counts[profile] = counts.get(profile, 0) + 1
    kinds = [a["actuator_type"] for a in actuators]
    print(f"Wrote campus_50 to {OUTPUT_DIR}")
    print(f"  nodes: {len(nodes)}  edges: {len(edges)}  connected: yes")
    print(f"  misting points: {kinds.count('misting_point')}  traffic signals: {kinds.count('traffic_signal')}")
    print(f"  shade profiles: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
