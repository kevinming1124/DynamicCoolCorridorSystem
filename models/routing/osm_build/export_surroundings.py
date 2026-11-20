"""Record each USC walkway's surface and nearby features from map.osm.

Run from the repository root after the USC dataset has been exported:

    python models/routing/osm_build/export_surroundings.py

Writes dataset/usc_osm_trimmed/surroundings.json and registers it in that
dataset's dataset.json. Uses only the Python standard library.

The exported walkway graph does not keep OSM way IDs, so every exported
segment is linked back to OpenStreetMap by position: OSM latitude/longitude
is projected to the dataset's UTM zone and shifted by its recorded origin.
For each segment the file stores:

- surface: the ``surface`` tag of the nearest walkable OSM way within
  MATCH_DISTANCE_M of the segment midpoint ("untagged" if that way has no
  tag, "unmatched" if no way is close enough)
- distances in metres from the segment to the nearest tree (single trees and
  tree rows), green area (grass, parks, gardens, grass pitches), building,
  parking area, major car road and fountain (null when none exists)

Environment turns these facts into a local air-temperature offset using the
rules in models/environment/config.json, so the rules can be tuned without
rebuilding this file. OSM multipolygon relations are skipped; only closed
ways count as areas.
"""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
OSM_FILE = HERE / "map.osm"
DATASET = HERE.parents[2] / "dataset" / "usc_osm_trimmed"
OUTPUT_NAME = "surroundings.json"

MATCH_DISTANCE_M = 10.0
SEARCH_RADIUS_M = 60.0          # features farther than this are not needed
WALKABLE = {"footway", "pedestrian", "path", "steps", "living_street", "service",
            "residential", "corridor", "track", "cycleway", "crossing"}
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary",
               "motorway_link", "trunk_link", "primary_link", "secondary_link"}
GREEN = {("landuse", "grass"), ("landuse", "recreation_ground"), ("landuse", "meadow"),
         ("leisure", "park"), ("leisure", "garden"), ("natural", "grassland"),
         ("natural", "wood"), ("natural", "scrub")}


# -- projection -----------------------------------------------------------------
def utm_xy(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """WGS84 latitude/longitude to UTM northern-hemisphere metres (Snyder 1987)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    big_a = math.cos(phi) * (lam - lam0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (big_a + (1 - t + c) * big_a ** 3 / 6
                  + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * big_a ** 5 / 120) + 500000.0
    y = k0 * (m + n * math.tan(phi) * (big_a ** 2 / 2
                                      + (5 - t + 9 * c + 4 * c * c) * big_a ** 4 / 24
                                      + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * big_a ** 6 / 720))
    return x, y


# -- geometry -------------------------------------------------------------------
def point_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    t = 0.0 if length_squared == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def _cross(ox, oy, ax, ay, bx, by):
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def segments_intersect(a, b, c, d):
    d1, d2 = _cross(*c, *d, *a), _cross(*c, *d, *b)
    d3, d4 = _cross(*a, *b, *c), _cross(*a, *b, *d)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def segment_segment(a, b, c, d):
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(point_segment(*a, *c, *d), point_segment(*b, *c, *d),
               point_segment(*c, *a, *b), point_segment(*d, *a, *b))


def point_in_polygon(x, y, ring):
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def near_bbox(box, segment_box, radius):
    return not (box[2] < segment_box[0] - radius or box[0] > segment_box[2] + radius
                or box[3] < segment_box[1] - radius or box[1] > segment_box[3] + radius)


def distance_to_shape(a, b, shape):
    """Segment A-B to a point, polyline or closed area (0 when inside)."""
    kind, points = shape["kind"], shape["points"]
    if kind == "point":
        return point_segment(*points[0], *a, *b)
    if kind == "area" and (point_in_polygon(*a, points) or point_in_polygon(*b, points)):
        return 0.0
    return min(segment_segment(a, b, p, q) for p, q in zip(points, points[1:]))


# -- OSM reading ----------------------------------------------------------------
def read_osm(zone: int, origin: tuple[float, float]) -> dict[str, list[dict]]:
    root = ET.parse(OSM_FILE).getroot()
    coords, layers = {}, {name: [] for name in
                          ("walkway", "tree", "green", "building", "parking", "major_road", "fountain")}

    def local(lat, lon):
        x, y = utm_xy(lat, lon, zone)
        return x - origin[0], y - origin[1]

    for node in root.iter("node"):
        xy = local(float(node.get("lat")), float(node.get("lon")))
        coords[node.get("id")] = xy
        tags = {t.get("k"): t.get("v") for t in node.iter("tag")}
        if tags.get("natural") == "tree":
            layers["tree"].append({"kind": "point", "points": [xy]})
        if tags.get("amenity") == "fountain":
            layers["fountain"].append({"kind": "point", "points": [xy]})
    for way in root.iter("way"):
        refs = [nd.get("ref") for nd in way.iter("nd") if nd.get("ref") in coords]
        if len(refs) < 2:
            continue
        points = [coords[r] for r in refs]
        tags = {t.get("k"): t.get("v") for t in way.iter("tag")}
        closed = refs[0] == refs[-1] and len(refs) >= 4
        kind = "area" if closed else "line"
        highway = tags.get("highway")
        if highway in WALKABLE and tags.get("area") != "yes":
            layers["walkway"].append({"kind": "line", "points": points,
                                      "surface": tags.get("surface", "untagged")})
        if highway in MAJOR_ROADS:
            layers["major_road"].append({"kind": "line", "points": points})
        if tags.get("natural") == "tree_row":
            layers["tree"].append({"kind": "line", "points": points})
        if closed and "building" in tags:
            layers["building"].append({"kind": "area", "points": points})
        if closed and tags.get("amenity") == "parking":
            layers["parking"].append({"kind": "area", "points": points})
        if closed and tags.get("amenity") == "fountain":
            layers["fountain"].append({"kind": "area", "points": points})
        grass_pitch = tags.get("leisure") == "pitch" and tags.get("surface") in (None, "grass")
        if closed and (grass_pitch or any(tags.get(k) == v for k, v in GREEN)):
            layers["green"].append({"kind": kind, "points": points})
    for shapes in layers.values():
        for shape in shapes:
            shape["bbox"] = bbox(shape["points"])
    return layers


def build() -> dict:
    manifest_path = DATASET / "dataset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    crs = manifest["graph"]["coordinate_system"]
    if crs.get("source_crs") != "EPSG:32611":
        raise ValueError(f"Expected UTM zone 11N (EPSG:32611), got {crs.get('source_crs')}")
    layers = read_osm(11, tuple(crs["origin"]))
    nodes = {n["node_id"]: (n["x_m"], n["y_m"])
             for n in json.loads((DATASET / "nodes.json").read_text(encoding="utf-8"))["nodes"]}
    edges = json.loads((DATASET / "edges.json").read_text(encoding="utf-8"))["edges"]

    records, match_distances = {}, []
    for edge in edges:
        a, b = nodes[edge["from_node"]], nodes[edge["to_node"]]
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        box = bbox([a, b])
        best = (math.inf, "unmatched")
        for way in layers["walkway"]:
            if near_bbox(way["bbox"], box, MATCH_DISTANCE_M):
                d = min(point_segment(*mid, *p, *q) for p, q in zip(way["points"], way["points"][1:]))
                if d < best[0]:
                    best = (d, way["surface"])
        surface = best[1] if best[0] <= MATCH_DISTANCE_M else "unmatched"
        if best[0] <= MATCH_DISTANCE_M:
            match_distances.append(best[0])
        record = {"surface": surface,
                  "matched_way_distance_m": round(best[0], 2) if best[0] <= MATCH_DISTANCE_M else None}
        for name in ("tree", "green", "building", "parking", "major_road", "fountain"):
            nearest = min((distance_to_shape(a, b, shape) for shape in layers[name]
                           if near_bbox(shape["bbox"], box, SEARCH_RADIUS_M)), default=None)
            record[f"{name}_m"] = None if nearest is None else round(nearest, 2)
        records[edge["edge_id"]] = record

    match_distances.sort()
    summary = {
        "segments": len(edges),
        "matched_segments": len(match_distances),
        "median_match_distance_m": round(match_distances[len(match_distances) // 2], 2) if match_distances else None,
        "feature_counts": {name: len(shapes) for name, shapes in layers.items()},
    }
    payload = {
        "description": "Per-segment surface and nearby OSM features for Environment's local "
                       "temperature offsets. Distances in metres; null means none in the file.",
        "source": "OpenStreetMap contributors (ODbL), models/routing/osm_build/map.osm",
        "match_distance_m": MATCH_DISTANCE_M,
        "search_radius_m": SEARCH_RADIUS_M,
        "summary": summary,
        "edges": records,
    }
    (DATASET / OUTPUT_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if manifest.get("files", {}).get("surroundings") != OUTPUT_NAME:
        manifest.setdefault("files", {})["surroundings"] = OUTPUT_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
