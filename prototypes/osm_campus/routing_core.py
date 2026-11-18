"""routingc's shared search and cost functions, without plotting/OSM imports.

The standalone real-campus prototype and the pipeline adapter use these exact
functions. A graph supplies the NetworkX-style edges interface the search reads.
"""

import heapq
from math import inf

K = 4


def make_cost(p):
    """Cost function for preference p (0 = pure speed, 1 = max comfort).
    cost = length * (1 + K * p * (1 - walkability))"""
    def cost(u, v, data):
        w = data.get("walkability", 1.0)
        return data["length"] * (1 + K * p * (1 - w))
    return cost


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


def set_blocked(G, u, v, k=0, blocked=True):
    G.edges[u, v, k]["blocked"] = blocked


def route(G, doors, start_building, dest_building, p):
    """Route from the start building's first door to the dest building's cheapest door."""
    start = doors[start_building][0]
    return dijkstra(G, start, doors[dest_building], make_cost(p))
