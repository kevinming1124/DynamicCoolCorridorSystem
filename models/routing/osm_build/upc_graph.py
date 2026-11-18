"""
Step 1 of the Cool Corridors project: build USC's UPC walkway graph from
OpenStreetMap. Building ENTRANCES are nodes (a building with 4 doors = 4 nodes);
the building itself is just a label that points to its entrance nodes.

Setup (once):   pip install osmnx matplotlib
Run:            python upc_graph.py
Needs osmnx 2.x (check with: python -c "import osmnx; print(osmnx.__version__)")

Reads everything from map.osm (exported from openstreetmap.org), so it runs
offline and doesn't depend on OSM's download servers.

Outputs (two versions of the graph):
  upc_walk_full.graphml     + buildings_full.json      every named building
  upc_walk_trimmed.graphml  + buildings_trimmed.json   only key_buildings.txt
  entrance_coverage.csv     where each building's entrances came from
  upc_graph_full.png, upc_graph_trimmed.png   pictures to sanity-check
  (buildings_*.json: building name -> list of its entrance node ids)

Inputs:
  key_buildings.txt       buildings to keep in the trimmed graph, one per line
Optional input:
  entrances_manual.csv    your own entrance list, columns: building,entrance,lat,lon
                          (building must match the OSM name exactly; see
                          entrance_coverage.csv for names). A building listed here
                          uses ONLY these entrances, replacing OSM/auto ones.
"""

import itertools
import json
import os
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points, substring

HERE = Path(__file__).resolve().parent
OSM_FILE = HERE / "map.osm"
ox.settings.cache_folder = HERE / "cache"
# The area we keep: (west, south, east, north), same numbers as the export.
# Anything outside gets cut off.
BBOX = (-118.29173, 34.01852, -118.27980, 34.02542)
AREA = box(*BBOX)  # the same rectangle as a shape

# OSM highway types a pedestrian can use
WALKABLE = {"footway", "pedestrian", "path", "steps", "living_street",
            "service", "residential", "corridor", "track"}

TOLERANCE = 15     # meters: junctions closer than this get merged
SNAP_SAME = 2.0    # meters: closer than this to an existing node = reuse that node
DOOR_RADIUS = 3.0  # meters: an OSM entrance this close to a building belongs to it
MANUAL_CSV = HERE / "entrances_manual.csv"
KEY_FILE = HERE / "key_buildings.txt"


def fetch(tags):
    """Read map features (buildings, entrances...) from the file, inside AREA."""
    return ox.features_from_xml(OSM_FILE, polygon=AREA, tags=tags)


def is_walkable(data):
    """Keep an edge if it's a walkable type and not marked private/no access."""
    return data.get("highway") in WALKABLE and data.get("access") not in ("private", "no")


# --- 1. Walking network, from the file ---------------------------------------
# Unsimplified at first (every OSM node kept) so the crop below is precise.
G = ox.graph_from_xml(OSM_FILE, bidirectional=True, simplify=False, retain_all=True)
print(f"All ways in file: {len(G.nodes)} nodes")

# (a) keep only walkable edges
keep = [(u, v, k) for u, v, k, d in G.edges(keys=True, data=True) if is_walkable(d)]
G = G.edge_subgraph(keep).copy()

# (b) crop to the rectangle: the export includes the FULL length of any way
#     touching it, e.g. long streets running kilometers west
G = ox.truncate.truncate_graph_bbox(G, BBOX)

# (c) keep the biggest connected piece; drops scraps a router couldn't reach
G = ox.truncate.largest_component(G)

# (d) now merge "drawing only" nodes into edge shapes (OSM nodes -> graph nodes)
G = ox.simplify_graph(G)
print(f"Walkable, cropped, connected: {len(G.nodes)} nodes")

# --- 2. Simplify -------------------------------------------------------------
G = ox.project_graph(G)  # coordinates in meters from here on
G = ox.consolidate_intersections(G, tolerance=TOLERANCE,
                                 rebuild_graph=True, dead_ends=False)
G = ox.convert.to_undirected(G)  # walking is two-way
CRS = G.graph["crs"]
print(f"Simplified ({TOLERANCE} m): {len(G.nodes)} junctions, {len(G.edges)} walkways")

# New nodes we create get integer ids after the existing ones.
new_id = itertools.count(max(G.nodes) + 1)


# --- 3. Helpers: put a point onto the walkway network -----------------------
def node_point(n):
    return Point(G.nodes[n]["x"], G.nodes[n]["y"])


def walkways():
    """All real walkway edges (not door connectors) as a table with shapes."""
    edges = ox.graph_to_gdfs(G, nodes=False)
    if "kind" in edges.columns:
        edges = edges[edges["kind"] != "connector"]
    return edges


def edge_line(u, v, data):
    """Walkway shape as a line. Straight segments have no stored geometry."""
    if "geometry" in data:
        return data["geometry"]
    return LineString([(G.nodes[u]["x"], G.nodes[u]["y"]),
                       (G.nodes[v]["x"], G.nodes[v]["y"])])


def node_on_walkway(target):
    """Return a node on the walkway closest to `target`.
    Reuses an existing junction if one is within SNAP_SAME meters; otherwise
    SPLITS the walkway at that spot (A----B becomes A--X--B, lengths exact)."""
    u, v, k = walkways().distance(target).idxmin()
    data = G.edges[u, v, k]
    line = edge_line(u, v, data)
    p = nearest_points(line, target)[0]
    d = line.project(p)  # meters along the walkway

    # The stored shape may run v->u; work out which node is at its start.
    sx, sy = line.coords[0]
    du = (G.nodes[u]["x"] - sx) ** 2 + (G.nodes[u]["y"] - sy) ** 2
    dv = (G.nodes[v]["x"] - sx) ** 2 + (G.nodes[v]["y"] - sy) ** 2
    start, end = (u, v) if du <= dv else (v, u)

    if d < SNAP_SAME:
        return start
    if line.length - d < SNAP_SAME:
        return end

    x = next(new_id)
    G.add_node(x, x=p.x, y=p.y, kind="split")
    extra = {key: val for key, val in data.items() if key not in ("geometry", "length")}
    part1, part2 = substring(line, 0, d), substring(line, d, line.length)
    G.remove_edge(u, v, k)
    G.add_edge(start, x, geometry=part1, length=part1.length, **extra)
    G.add_edge(x, end, geometry=part2, length=part2.length, **extra)
    return x


def add_entrance(building, label, door_pt, source):
    """Make an entrance node at the real door location, linked to the walkway."""
    w = node_on_walkway(door_pt)
    gap = door_pt.distance(node_point(w))
    if gap < SNAP_SAME and G.nodes[w].get("kind") != "entrance":
        n = w  # the walkway runs right up to the door: that node IS the entrance
    else:
        n = next(new_id)
        G.add_node(n, x=door_pt.x, y=door_pt.y)
        G.add_edge(n, w, length=gap, kind="connector")
    G.nodes[n].update(kind="entrance", building=building, label=label, source=source)
    return n


def text(val):
    return val if isinstance(val, str) and val.strip() else None


# --- 4. Gather buildings and entrances ---------------------------------------
buildings = fetch({"building": True})
buildings = buildings[buildings["name"].notna()]  # unnamed sheds/annexes would flood the graph
buildings = buildings[buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
buildings = buildings.drop_duplicates(subset="name").to_crs(CRS)
print(f"Named buildings: {len(buildings)}")

try:
    osm_doors = fetch({"entrance": True})
    osm_doors = osm_doors[osm_doors.geometry.geom_type == "Point"].to_crs(CRS)
except Exception:
    osm_doors = gpd.GeoDataFrame(geometry=[], crs=CRS)
print(f"OSM entrance points found: {len(osm_doors)}")

manual = {}
if os.path.exists(MANUAL_CSV):
    df = pd.read_csv(MANUAL_CSV)
    mg = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]),
                          crs="EPSG:4326").to_crs(CRS)
    for b, grp in mg.groupby("building"):
        manual[b] = list(zip(grp["entrance"], grp.geometry))
    unknown = set(manual) - set(buildings["name"])
    if unknown:
        print("WARNING: these manual buildings don't match any OSM name:", sorted(unknown))

# --- 5. Add each building's entrances ---------------------------------------
lookup, report = {}, []
for name, fp in zip(buildings["name"], buildings.geometry):
    if name in manual:
        source, doors = "manual", manual[name]
    else:
        near = osm_doors[osm_doors.within(fp.buffer(DOOR_RADIUS))] if len(osm_doors) else []
        if len(near):
            source = "osm"
            doors = []
            for i, (_, row) in enumerate(near.iterrows()):
                r = row.to_dict()
                label = text(r.get("name")) or text(r.get("ref")) or f"door {i + 1}"
                doors.append((label, row.geometry))
        else:
            # No known doors: assume one, on the side of the building nearest a walkway.
            edges = walkways()
            u, v, k = edges.distance(fp).idxmin()
            line = edge_line(u, v, G.edges[u, v, k])
            source, doors = "auto", [("auto door", nearest_points(fp, line)[0])]

    lookup[name] = [int(add_entrance(name, label, pt, source)) for label, pt in doors]
    report.append({"building": name, "source": source, "entrances": len(doors)})

# --- 6. Report ---------------------------------------------------------------
rep = pd.DataFrame(report).sort_values(["source", "building"])
rep.to_csv(HERE / "entrance_coverage.csv", index=False)
print("\nEntrance coverage (buildings per source):")
print(rep.groupby("source").agg(buildings=("building", "count"), entrances=("entrances", "sum")))
print("-> 'auto' buildings got a single guessed door; fix important ones in", MANUAL_CSV)

def describe(graph, label):
    n_doors = sum(1 for _, d in graph.nodes(data=True) if d.get("kind") == "entrance")
    print(f"{label}: {len(graph.nodes)} nodes ({n_doors} entrances), {len(graph.edges)} edges")


def save(graph, doors, suffix):
    ox.save_graphml(graph, HERE / f"upc_walk_{suffix}.graphml")
    with open(HERE / f"buildings_{suffix}.json", "w") as f:
        json.dump(doors, f, indent=2, sort_keys=True)
    print(f"Saved: upc_walk_{suffix}.graphml, buildings_{suffix}.json")


def plot(graph, suffix):
    """Grey junctions; entrances colored by where they came from."""
    color = {"osm": "red", "manual": "blue", "auto": "purple"}
    nodes = list(graph.nodes(data=True))
    node_colors = [color[d["source"]] if d.get("kind") == "entrance" else "#999999" for _, d in nodes]
    node_sizes = [14 if d.get("kind") == "entrance" else 4 for _, d in nodes]
    path = HERE / f"upc_graph_{suffix}.png"
    try:
        ox.plot_graph(graph, node_size=node_sizes, node_color=node_colors, edge_linewidth=0.7,
                      save=True, filepath=path, show=False)
        print(f"Saved plot: {path}  (red = OSM door, blue = manual, purple = guessed)")
    except OSError as e:
        # On Windows this usually means the old PNG is open in a viewer or OneDrive is syncing it
        print(f"WARNING: couldn't save {path} (close it if it's open, then rerun):", e)


print()
describe(G, "Full graph")
save(G, lookup, "full")   # save before anything else can go wrong


# --- 7. Trimmed version: only key buildings ----------------------------------
def line_from(graph, a, b, data):
    """Edge a-b as a line that runs FROM a TO b (stored shapes can run either way)."""
    if "geometry" in data:
        line = data["geometry"]
    else:
        line = LineString([(graph.nodes[a]["x"], graph.nodes[a]["y"]),
                           (graph.nodes[b]["x"], graph.nodes[b]["y"])])
    sx, sy = line.coords[0]
    ax, ay = graph.nodes[a]["x"], graph.nodes[a]["y"]
    bx, by = graph.nodes[b]["x"], graph.nodes[b]["y"]
    if (ax - sx) ** 2 + (ay - sy) ** 2 > (bx - sx) ** 2 + (by - sy) ** 2:
        line = LineString(list(line.coords)[::-1])
    return line


def trim(full, doors, keep):
    T = full.copy()
    keep_doors = {n for b in keep for n in doors[b]}

    # (a) Drop doors of buildings we're not keeping.
    for n, d in list(T.nodes(data=True)):
        if d.get("kind") != "entrance" or n in keep_doors:
            continue
        on_walkway = any(ed.get("kind") != "connector" for _, _, ed in T.edges(n, data=True))
        if on_walkway:
            # the walkway itself ran to this door: keep the node, just un-label it
            for key in ("kind", "building", "label", "source"):
                d.pop(key, None)
        else:
            T.remove_node(n)  # a separate door node on a connector: remove both

    # (b) Repeatedly remove dead ends that don't lead to a kept door.
    # First drop self-loops (an edge from a node back to itself, left over when
    # the 15 m merge squashed a little loop of path): no route ever uses one.
    T.remove_edges_from([(u, v, k) for u, v, k in T.edges(keys=True) if u == v])
    # A dead end = a node with at most ONE distinct neighbor. (Counting edges
    # instead would miss a stub joined by two parallel edges.)
    while True:
        dead = [n for n in T.nodes
                if len(set(T.neighbors(n)) - {n}) <= 1 and n not in keep_doors]
        if not dead:
            break
        T.remove_nodes_from(dead)

    # (c) Merge pass-through nodes (exactly 2 edges, not a door) into one longer edge.
    for n in list(T.nodes):
        if n in keep_doors or T.degree(n) != 2:
            continue
        (_, a, ka, da), (_, b, kb, db) = list(T.edges(n, keys=True, data=True))
        if a == b or a == n or b == n:
            continue  # a little loop; merging would make an edge from a node to itself
        l1, l2 = line_from(T, a, n, da), line_from(T, n, b, db)
        merged = LineString(list(l1.coords) + list(l2.coords)[1:])
        extra = {k: v for k, v in da.items() if k not in ("geometry", "length", "kind")}
        T.remove_node(n)
        T.add_edge(a, b, geometry=merged, length=da["length"] + db["length"], **extra)

    return T, {b: doors[b] for b in keep}


if os.path.exists(KEY_FILE):
    with open(KEY_FILE, encoding="utf-8") as f:
        wanted = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    keep = [b for b in wanted if b in lookup]
    missing = [b for b in wanted if b not in lookup]
    if missing:
        print("WARNING: not found in OSM building names (check spelling):", missing)
    T, lookup_trimmed = trim(G, lookup, keep)
    describe(T, f"Trimmed graph ({len(keep)} key buildings)")
    save(T, lookup_trimmed, "trimmed")
else:
    T = None
    print(f"(No {KEY_FILE} found, so no trimmed graph made.)")

# --- 8. Pictures ---------------------------------------------------------------
plot(G, "full")
if T is not None:
    plot(T, "trimmed")
