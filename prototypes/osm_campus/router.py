"""
Cool Corridors router: comfort-vs-speed routing on the UPC walkway graph.

Run (from the Cool Corridors folder, after upc_graph.py has made the graph):
    python router.py
    python router.py --start "Taper Hall" --dest "Ginsburg Hall"
    python router.py --graph full --block

Prints each route's stats and saves routes.png.
"""

import argparse
import heapq
import json
import random
from math import inf, sin, cos

import matplotlib.pyplot as plt
import osmnx as ox
from shapely.geometry import LineString

K = 4             # how far comfort-seekers will detour: worst edge "feels" (1+K)x its length
WALK_SPEED = 1.4  # meters per second, typical walking pace


# --- Piece 1: edge cost --------------------------------------------------------
def make_cost(p):
    """Cost function for preference p (0 = pure speed, 1 = max comfort).
    cost = length * (1 + K * p * (1 - walkability))"""
    def cost(u, v, data):
        w = data.get("walkability", 1.0)
        return data["length"] * (1 + K * p * (1 - w))
    return cost


# --- Piece 2: Dijkstra (stops at the first of several target nodes) -----------
def dijkstra(G, start, targets, cost):
    """Returns (edges used as [(u, v, key), ...], total cost), or (None, inf)."""
    targets = set(targets)
    best = {start: 0.0}           # cheapest known cost to each node
    came_from = {start: None}     # node -> (previous node, edge key)
    heap = [(0.0, start)]         # (cost so far, node), cheapest popped first
    done = set()

    while heap:
        c, u = heapq.heappop(heap)
        if u in done:
            continue              # outdated entry (lazy deletion)
        done.add(u)

        if u in targets:          # first door reached = cheapest door
            edges, node = [], u
            while came_from[node] is not None:
                prev, k = came_from[node]
                edges.append((prev, node, k))
                node = prev
            return edges[::-1], c

        for _, v, k, data in G.edges(u, keys=True, data=True):
            if data.get("blocked"):
                continue          # Piece 3: closed walkway
            new = c + cost(u, v, data)
            if new < best.get(v, inf):
                best[v] = new
                came_from[v] = (u, k)
                heapq.heappush(heap, (new, v))

    return None, inf


# --- Piece 3: blocking ---------------------------------------------------------
def set_blocked(G, u, v, k=0, blocked=True):
    G.edges[u, v, k]["blocked"] = blocked


# --- Helpers -------------------------------------------------------------------
def route(G, doors, start_building, dest_building, p):
    """Route from the start building's first door to the dest building's cheapest door."""
    start = doors[start_building][0]
    return dijkstra(G, start, doors[dest_building], make_cost(p))


def stats(G, edges):
    dist = sum(G.edges[e]["length"] for e in edges)
    exposure = sum(G.edges[e]["length"] * (1 - G.edges[e]["walkability"]) for e in edges)
    return dist, exposure


def edge_line(G, u, v, data):
    if "geometry" in data:
        return data["geometry"]
    return LineString([(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])


def placeholder_walkability(G, seed=0):
    """FAKE scores in [0, 1] until the real model exists. Smooth patches of shade
    plus a little noise, so nearby edges have similar values, like real shade."""
    rng = random.Random(seed)
    for u, v, k, data in G.edges(keys=True, data=True):
        mid = edge_line(G, u, v, data).interpolate(0.5, normalized=True)
        smooth = 0.55 + 0.35 * sin(mid.x / 80) * cos(mid.y / 110)
        data["walkability"] = min(1.0, max(0.0, smooth + rng.uniform(-0.1, 0.1)))


# --- Piece 4: run and draw -----------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", choices=["trimmed", "full"], default="trimmed")
    ap.add_argument("--start", default="Leavey Library")
    ap.add_argument("--dest", default="Seeley G. Mudd Building")
    ap.add_argument("--block", action="store_true",
                    help="also block the busiest edge of the shortest route and replan")
    ap.add_argument("--heat-test", action="store_true",
                    help="make every edge of the shortest route maximally hot, to check "
                         "that comfort routing really detours")
    args = ap.parse_args()

    G = ox.load_graphml(f"upc_walk_{args.graph}.graphml")
    with open(f"buildings_{args.graph}.json") as f:
        doors = {b: [int(n) for n in ns] for b, ns in json.load(f).items()}
    for b in (args.start, args.dest):
        if b not in doors:
            raise SystemExit(f"'{b}' isn't in buildings_{args.graph}.json - check the spelling")

    placeholder_walkability(G)

    if args.heat_test:
        # Controlled test: find the shortest route, then make all of it as hot as
        # possible (walkability 0). Speed mode should still take it (it ignores heat);
        # comfort mode should now avoid it if any alternative is within its detour limit.
        hot, _ = route(G, doors, args.start, args.dest, 0.0)
        if hot:
            for e in hot:
                G.edges[e]["walkability"] = 0.0
            print(f"\nHEAT TEST: set walkability = 0 on all {len(hot)} edges of the shortest route")

    settings = [("speed (p=0)", 0.0, "#2b6cb0"),
                ("balanced (p=0.5)", 0.5, "#d69e2e"),
                ("comfort (p=1)", 1.0, "#2f855a")]
    results = []
    print(f"\n{args.start}  ->  {args.dest}\n")
    print(f"{'setting':<18}{'distance':>10}{'time':>9}{'heat exposure':>15}")
    for label, p, color in settings:
        edges, _ = route(G, doors, args.start, args.dest, p)
        if edges is None:
            print(f"{label:<18}  no route")
            continue
        d, e = stats(G, edges)
        print(f"{label:<18}{d:>8.0f} m{d / WALK_SPEED / 60:>7.1f} min{e:>13.0f}")
        results.append((label, color, edges))

    if args.block and results:
        # Close the longest edge on the shortest route and see what happens.
        u, v, k = max(results[0][2], key=lambda e: G.edges[e]["length"])
        set_blocked(G, u, v, k)
        edges, _ = route(G, doors, args.start, args.dest, 0.0)
        if edges is None:
            print("\nAfter blocking: no route at all!")
        else:
            d, e = stats(G, edges)
            print(f"\n{'speed, 1 blocked':<18}{d:>8.0f} m{d / WALK_SPEED / 60:>7.1f} min{e:>13.0f}")
            results.append(("speed, after block", "#c53030", edges))

    # Draw: every edge colored by walkability, routes on top, blocked edges dashed.
    fig, ax = plt.subplots(figsize=(12, 9))
    cmap = plt.get_cmap("RdYlGn")
    for u, v, k, data in G.edges(keys=True, data=True):
        line = edge_line(G, u, v, data)
        xs, ys = line.xy
        style = "--" if data.get("blocked") else "-"
        ax.plot(xs, ys, style, color=cmap(data["walkability"]), linewidth=1.2, alpha=0.6)
    for i, (label, color, edges) in enumerate(results):
        for j, e in enumerate(edges):
            xs, ys = edge_line(G, e[0], e[1], G.edges[e]).xy
            ax.plot(xs, ys, color=color, linewidth=5 - i, alpha=0.85,
                    label=label if j == 0 else None)
    for name, mark in ((args.start, "o"), (args.dest, "*")):
        for n in doors[name]:
            ax.plot(G.nodes[n]["x"], G.nodes[n]["y"], mark, color="black", markersize=12)
    ax.set_title(f"{args.start} -> {args.dest}\n"
                 "thin lines: walkability (red = hot, green = comfortable)")
    ax.legend(loc="lower left")
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig("routes.png", dpi=150, bbox_inches="tight")
    print("\nSaved routes.png")


if __name__ == "__main__":
    main()
