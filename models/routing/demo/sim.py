"""
Cool Corridors simulation: pedestrians walking the UPC graph, animated.

Needs router.py in the same folder (it reuses its Dijkstra and cost formula).

Run:
    python sim.py                    # 10 agents, trimmed graph, one blocking event
    python sim.py --agents 25 --graph full
    python sim.py --block-at 0       # no blocking event
Saves sim.gif (play it like a video) and prints each agent's trip stats.
"""

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import osmnx as ox
from matplotlib.animation import FuncAnimation, PillowWriter
from shapely.geometry import LineString

if __package__:
    from .router import GRAPH_DIRECTORY, HERE, WALK_SPEED, dijkstra, edge_line, make_cost, placeholder_walkability, set_blocked
else:
    from router import GRAPH_DIRECTORY, HERE, WALK_SPEED, dijkstra, edge_line, make_cost, placeholder_walkability, set_blocked

DT = 5  # seconds per simulation step (small enough for smooth motion)


# --- Piece 1: the agent ----------------------------------------------------------
@dataclass
class Agent:
    id: int
    origin: str
    dest: str
    p: float                 # 0 = pure speed, 1 = max comfort
    start_node: int
    depart: float            # seconds after the simulation starts
    route: list = field(default_factory=list)   # [(u, v, k), ...] in walking direction
    edge_i: int = 0          # index into route of the edge it's on
    offset: float = 0.0      # meters along that edge
    status: str = "waiting"  # waiting / walking / arrived / stuck
    walked: float = 0.0
    exposure: float = 0.0
    replans: int = 0
    arrived_at: float = None


# --- Piece 2: moving -------------------------------------------------------------
def step(agent, G, dt, t):
    budget = WALK_SPEED * dt
    while budget > 1e-9 and agent.status == "walking":
        e = agent.route[agent.edge_i]
        data = G.edges[e]
        left = data["length"] - agent.offset
        move = min(budget, left)
        agent.offset += move
        agent.walked += move
        agent.exposure += move * (1 - data["walkability"])
        budget -= move
        if move >= left - 1e-9:                 # reached the end of this edge
            agent.edge_i += 1
            agent.offset = 0.0
            if agent.edge_i == len(agent.route):
                agent.status = "arrived"
                agent.arrived_at = t + dt - budget / WALK_SPEED


def line_from(G, a, b, data):
    """Edge a-b as a line running FROM a TO b (stored shapes can run either way)."""
    line = edge_line(G, a, b, data)
    sx, sy = line.coords[0]
    da = (G.nodes[a]["x"] - sx) ** 2 + (G.nodes[a]["y"] - sy) ** 2
    db = (G.nodes[b]["x"] - sx) ** 2 + (G.nodes[b]["y"] - sy) ** 2
    return line if da <= db else LineString(list(line.coords)[::-1])


def position(agent, G):
    u, v, k = agent.route[agent.edge_i]
    pt = line_from(G, u, v, G.edges[u, v, k]).interpolate(agent.offset)
    return pt.x, pt.y


# --- Piece 3: the simulation loop ------------------------------------------------
def plan(agent, G, doors):
    route, _ = dijkstra(G, agent.start_node, doors[agent.dest], make_cost(agent.p))
    if route is None:
        agent.status = "stuck"
    elif not route:                             # already standing at a destination door
        agent.status = "arrived"
    else:
        agent.route, agent.status = route, "walking"


def replan(agent, G, doors):
    """Finish the current edge, then follow a fresh route from its end node."""
    u, v, k = agent.route[agent.edge_i]
    rest, _ = dijkstra(G, v, doors[agent.dest], make_cost(agent.p))
    agent.replans += 1
    if rest is None:
        agent.status = "stuck"
    else:
        agent.route = agent.route[:agent.edge_i + 1] + rest


def norm(e):
    """Same edge regardless of walking direction: (u, v, k) == (v, u, k)."""
    u, v, k = e
    return (min(u, v), max(u, v), k)


def run(G, doors, n_agents, block_at, seed):
    rng = random.Random(seed)
    buildings = sorted(doors)
    agents = []
    for i in range(n_agents):
        origin, dest = rng.sample(buildings, 2)
        agents.append(Agent(id=i, origin=origin, dest=dest, p=round(rng.random(), 2),
                            start_node=rng.choice(doors[origin]),
                            depart=rng.uniform(0, 180)))

    frames, events, t = [], [], 0.0
    while t < 3 * 3600:                         # safety cap: 3 simulated hours
        for a in agents:
            if a.status == "waiting" and t >= a.depart:
                plan(a, G, doors)
            if a.status == "walking":
                step(a, G, DT, t)

        # Event: block the edge that the most walking agents still plan to use.
        if block_at and abs(t - block_at) < DT / 2:
            upcoming = Counter(norm(e) for a in agents if a.status == "walking"
                               for e in a.route[a.edge_i + 1:])
            if upcoming:
                (u, v, k), users = upcoming.most_common(1)[0]
                set_blocked(G, u, v, k)
                hit = [a for a in agents if a.status == "walking"
                       and any(G.edges[e].get("blocked") for e in a.route[a.edge_i + 1:])]
                for a in hit:
                    replan(a, G, doors)
                events.append((len(frames), (u, v, k)))
                print(f"t = {t / 60:4.1f} min: blocked a walkway {users} agent(s) were "
                      f"about to use; replanned agents {[a.id for a in hit]}")
            else:
                print(f"t = {t / 60:4.1f} min: no one walking yet, so nothing to block")

        frames.append((t, [(*position(a, G), a.p) for a in agents if a.status == "walking"],
                       Counter(a.status for a in agents)))
        t += DT
        if all(a.status in ("arrived", "stuck") for a in agents):
            break
    return agents, frames, events


# --- Piece 4: the animation ------------------------------------------------------
def animate(G, frames, events, path):
    fig, ax = plt.subplots(figsize=(10, 7.5))
    heat = plt.get_cmap("RdYlGn")
    for u, v, k, data in G.edges(keys=True, data=True):
        xs, ys = edge_line(G, u, v, data).xy
        ax.plot(xs, ys, color=heat(data["walkability"]), linewidth=1.2, alpha=0.55, zorder=1)

    # Blocked walkways: drawn as thick black dashes, appearing when they get blocked.
    block_lines = []
    for frame_i, (u, v, k) in events:
        xs, ys = edge_line(G, u, v, G.edges[u, v, k]).xy
        (ln,) = ax.plot(xs, ys, "--", color="black", linewidth=3, zorder=2, visible=False)
        block_lines.append((frame_i, ln))

    dots = ax.scatter([], [], s=70, c=[], cmap="cool", vmin=0, vmax=1,
                      edgecolors="black", linewidths=0.8, zorder=3)
    clock = ax.text(0.01, 0.99, "", transform=ax.transAxes, va="top", fontsize=11,
                    family="monospace", bbox=dict(facecolor="white", alpha=0.8, edgecolor="none"))
    ax.set_title("Walkways: red = hot, green = comfortable    "
                 "Agents: cyan = speed-seeker, magenta = comfort-seeker", fontsize=10)
    ax.set_aspect("equal")
    ax.axis("off")

    def update(i):
        t, people, counts = frames[i]
        if people:
            dots.set_offsets([(x, y) for x, y, _ in people])
            dots.set_array([p for _, _, p in people])
        else:
            dots.set_offsets([[float("nan"), float("nan")]])
        for frame_i, ln in block_lines:
            ln.set_visible(i >= frame_i)
        m, s = divmod(int(t), 60)
        clock.set_text(f"t = {m:02d}:{s:02d}   walking {counts['walking']:>2}   "
                       f"arrived {counts['arrived']:>2}   stuck {counts['stuck']}")
        return dots, clock

    anim = FuncAnimation(fig, update, frames=len(frames), interval=50)
    anim.save(path, writer=PillowWriter(fps=20))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", choices=["trimmed", "full"], default="trimmed")
    ap.add_argument("--agents", type=int, default=10)
    ap.add_argument("--block-at", type=float, default=4,
                    help="minute at which to block a busy walkway (0 = never)")
    ap.add_argument("--seed", type=int, default=1, help="change for a different random run")
    args = ap.parse_args()

    G = ox.load_graphml(GRAPH_DIRECTORY / f"upc_walk_{args.graph}.graphml")
    with open(GRAPH_DIRECTORY / f"buildings_{args.graph}.json") as f:
        doors = {b: [int(n) for n in ns] for b, ns in json.load(f).items()}
    placeholder_walkability(G)

    agents, frames, events = run(G, doors, args.agents, args.block_at * 60, args.seed)

    print(f"\n{'id':>2}  {'p':>4}  {'trip':<62}{'dist':>7}{'min':>6}{'exposure':>10}{'replans':>8}  status")
    for a in agents:
        trip = f"{a.origin[:29]} -> {a.dest[:29]}"
        mins = f"{(a.arrived_at - a.depart) / 60:.1f}" if a.arrived_at is not None else "-"
        print(f"{a.id:>2}  {a.p:>4.2f}  {trip:<62}{a.walked:>6.0f}m{mins:>6}{a.exposure:>10.0f}"
              f"{a.replans:>8}  {a.status}")

    print(f"\nRendering {len(frames)} frames ({len(frames) * DT / 60:.0f} simulated minutes)...")
    try:
        animate(G, frames, events, HERE / "sim.gif")
        print(f"Saved {HERE / 'sim.gif'}")
    except OSError as e:
        print("WARNING: couldn't save sim.gif (close it if it's open, then rerun):", e)


if __name__ == "__main__":
    main()
