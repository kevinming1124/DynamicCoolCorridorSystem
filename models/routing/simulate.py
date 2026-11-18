"""Pedestrian simulation: people walking their routes across campus.

    python -m models.routing.simulate
    python -m models.routing.simulate --agents 25 --time 15:00 --scenario extreme_heat
    python -m models.routing.simulate --block-at 0          # no walkway closure
    python -m models.routing.simulate --animate             # also save a GIF

What happens:

1. Run the normal pipeline once (Control -> Environment -> Heat Exposure ->
   Routing) at the chosen time and scenario, so heat values and crosswalk
   waits are real.
2. Create N people. Each gets a random start and destination place, a random
   comfort-vs-speed preference, and a departure time in the first 3 minutes.
3. Step time forward a few seconds at a time. People plan a route when they
   leave, walk it, wait at crosswalks, and replan if a walkway ahead closes.
4. Print each person's trip, and compare comfort-seekers with speed-seekers:
   how much heat did caring about comfort actually save, and what did it cost
   in time?

Position is stored as (which step of the route, meters along that edge), not
as x/y. That keeps people exactly on walkways and makes moving simple: add
meters. x/y is only worked out when drawing a frame.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from utils import load_campus_graph

from .dijkstra import RouteStep, resolve_nodes
from .model import DEFAULT_DATASET_PATH, Planner, RoutingSettings

EPS = 1e-9   # tolerance for "basically zero" when comparing floats


# ---------------------------------------------------------------------------
# The agent (one simulated person)
# ---------------------------------------------------------------------------
@dataclass
class Agent:
    id: int
    start: str
    destination: str
    preference: float          # 0 = fastest, 1 = most comfortable
    depart_s: float            # seconds after the simulation starts
    start_node: str
    targets: set[str]          # destination node(s): any entrance will do

    steps: list[RouteStep] = field(default_factory=list)
    step_i: int = 0            # which step of the route they're on
    offset_m: float = 0.0      # meters along that step's edge
    status: str = "waiting"    # waiting / walking / at_signal / arrived / stuck
    wait_left_s: float = 0.0   # time still to wait at the current crosswalk
    wait_rate: float = 0.0     # heat rate while waiting (the edge they arrived on)

    walked_m: float = 0.0
    exposure: float = 0.0      # degree-minutes of heat, the main result
    signal_wait_s: float = 0.0
    replans: int = 0
    left_s: float | None = None    # when they actually set off (the first tick after depart_s)
    arrived_s: float | None = None

    # The fastest route for the same trip, planned at departure, for comparison.
    baseline_minutes: float = 0.0
    baseline_exposure: float = 0.0

    @property
    def trip_minutes(self) -> float | None:
        if self.arrived_s is None or self.left_s is None:
            return None
        # Timed from when they actually set off, so the simulation's time
        # step doesn't count as part of the trip.
        return (self.arrived_s - self.left_s) / 60.0


@dataclass
class SimulationResult:
    agents: list[Agent]
    frames: list[tuple[float, list[tuple[float, float, float]], Counter]]
    closures: list[tuple[int, str]]   # (frame index, edge ID) when each walkway closed
    planner: Planner


# ---------------------------------------------------------------------------
# Planning and replanning
# ---------------------------------------------------------------------------
def plan(agent: Agent, planner: Planner, now_s: float) -> None:
    """The agent leaves: plan their route, and the fastest route for comparison."""
    agent.left_s = now_s
    result = planner.route_nodes(agent.start_node, agent.targets, agent.preference,
                                 start_min=now_s / 60)
    if result is None:
        agent.status = "stuck"
        return
    agent.steps = list(result.steps)
    agent.status = "walking" if agent.steps else "arrived"
    if not agent.steps:
        agent.arrived_s = now_s

    fastest = planner.route_nodes(agent.start_node, agent.targets, 0.0, start_min=now_s / 60)
    stats = planner.stats(fastest, agent.targets)
    agent.baseline_minutes, agent.baseline_exposure = stats.minutes, stats.exposure


def remaining_edges(agent: Agent) -> list[str]:
    """Edges still ahead of the agent (not counting the one they're walking on)."""
    if agent.status == "walking":
        return [s.edge_id for s in agent.steps[agent.step_i + 1:]]
    if agent.status == "at_signal":
        return [s.edge_id for s in agent.steps[agent.step_i:]]
    return []


def replan(agent: Agent, planner: Planner, now_s: float) -> None:
    """A walkway ahead closed: find a new way from the next node.

    A walking agent finishes the edge they're on (no U-turns mid-walkway), then
    follows a fresh route from its end. An agent waiting at a crosswalk is
    already standing on a node, so they replan from there.
    """
    if agent.status == "walking":
        keep = agent.steps[: agent.step_i + 1]
        from_node = agent.steps[agent.step_i].to_node
    else:  # at_signal
        keep = agent.steps[: agent.step_i]
        from_node = agent.steps[agent.step_i].from_node
    agent.replans += 1
    new = planner.route_nodes(from_node, agent.targets, agent.preference, start_min=now_s / 60)
    if new is None:
        agent.status = "stuck"
    else:
        agent.steps = keep + list(new.steps)


# ---------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------
def advance(agent: Agent, planner: Planner, now_s: float, dt: float) -> None:
    """Move one agent forward by dt seconds: walking and/or waiting at crosswalks.

    A `while` loop spends the time step: if the agent finishes an edge with
    time to spare, they carry on onto the next one in the same step, so they
    never pause at junctions.
    """
    time_left = dt
    while time_left > EPS and agent.status in ("walking", "at_signal"):
        if agent.status == "at_signal":
            used = min(time_left, agent.wait_left_s)
            agent.wait_left_s -= used
            agent.signal_wait_s += used
            agent.exposure += agent.wait_rate * used / 60.0   # standing in the heat
            time_left -= used
            if agent.wait_left_s <= EPS:
                agent.status = "walking"
            continue

        step = agent.steps[agent.step_i]
        edge = planner.edges[step.edge_id]
        rate = planner.heat.rate(step.edge_id)
        # Walk each edge in exactly the dataset's walk_time_min, so the
        # simulation and the planner agree to the second. (The dataset rounds
        # walking times, so a fixed 1.4 m/s would drift by a fraction of a percent.)
        speed = edge.distance_m / (edge.walk_time_min * 60.0)
        time_to_end = (edge.distance_m - agent.offset_m) / speed
        used = min(time_left, time_to_end)
        agent.offset_m += used * speed
        agent.walked_m += used * speed
        agent.exposure += rate * used / 60.0                 # rate is per minute
        time_left -= used

        if used >= time_to_end - EPS:                        # reached the end of this edge
            agent.step_i += 1
            agent.offset_m = 0.0
            if agent.step_i == len(agent.steps):
                agent.status = "arrived"
                agent.arrived_s = now_s + dt - time_left
            elif step.to_node in planner.signal_wait_min and step.to_node not in agent.targets:
                agent.status = "at_signal"
                agent.wait_left_s = planner.signal_wait_min[step.to_node] * 60.0
                agent.wait_rate = rate


def position(agent: Agent, planner: Planner) -> tuple[float, float]:
    """The agent's x/y in meters, for drawing."""
    nodes = planner.graph.nodes
    if agent.status == "at_signal":
        node = nodes[agent.steps[agent.step_i].from_node]
        return node.x_m, node.y_m
    step = agent.steps[agent.step_i]
    a, b = nodes[step.from_node], nodes[step.to_node]
    fraction = agent.offset_m / planner.edges[step.edge_id].distance_m
    # campus_50 edges are straight lines, so x/y is a simple blend of the ends.
    return a.x_m + fraction * (b.x_m - a.x_m), a.y_m + fraction * (b.y_m - a.y_m)


# ---------------------------------------------------------------------------
# The main loop
# ---------------------------------------------------------------------------
def run(
    planner: Planner,
    n_agents: int = 10,
    block_at_min: float = 4.0,
    seed: int = 1,
    dt: float = 5.0,
    max_minutes: float = 120.0,
) -> SimulationResult:
    rng = random.Random(seed)
    places = planner.named_places()
    agents = []
    for i in range(n_agents):
        start, destination = rng.sample(places, 2)
        agents.append(Agent(
            id=i, start=start, destination=destination,
            preference=round(rng.random(), 2),
            depart_s=rng.uniform(0, 180),
            start_node=sorted(resolve_nodes(planner.graph, start))[0],
            targets=resolve_nodes(planner.graph, destination),
        ))

    frames, closures, t = [], [], 0.0
    block_done = not block_at_min
    while t <= max_minutes * 60:
        for agent in agents:
            if agent.status == "waiting" and t >= agent.depart_s:
                plan(agent, planner, t)
            advance(agent, planner, t, dt)

        if not block_done and t >= block_at_min * 60:
            block_done = True
            _close_busiest_walkway(agents, planner, t, len(frames), closures)

        frames.append((
            t,
            [(*position(a, planner), a.preference) for a in agents
             if a.status in ("walking", "at_signal")],
            Counter(a.status for a in agents),
        ))
        if all(a.status in ("arrived", "stuck") for a in agents):
            break
        t += dt
    return SimulationResult(agents, frames, closures, planner)


def _close_busiest_walkway(agents, planner, t, frame_i, closures) -> None:
    """Close the walkway the most people are about to use, so replanning shows."""
    ahead = Counter(e for a in agents for e in remaining_edges(a) if e not in planner.blocked)
    if not ahead:
        print(f"t = {t / 60:4.1f} min: nobody has a walkway ahead, so nothing to close")
        return
    edge_id, users = ahead.most_common(1)[0]
    planner.block(edge_id)
    affected = [a for a in agents if edge_id in remaining_edges(a)]
    for agent in affected:
        replan(agent, planner, t)
    closures.append((frame_i, edge_id))
    print(f"t = {t / 60:4.1f} min: closed walkway {edge_id} ({users} people were about "
          f"to use it); replanned people {[a.id for a in affected]}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def report(result: SimulationResult) -> None:
    print(f"\n{'id':>3} {'pref':>5}  {'trip':<50}{'min':>6}{'fastest':>8}"
          f"{'exposure':>10}{'fastest':>8}{'signal s':>9}{'replans':>8}  status")
    for a in result.agents:
        trip = f"{a.start[:23]} -> {a.destination[:23]}"
        minutes = f"{a.trip_minutes:.1f}" if a.trip_minutes is not None else "-"
        print(f"{a.id:>3} {a.preference:>5.2f}  {trip:<50}{minutes:>6}{a.baseline_minutes:>8.1f}"
              f"{a.exposure:>10.1f}{a.baseline_exposure:>8.1f}{a.signal_wait_s:>9.0f}"
              f"{a.replans:>8}  {a.status}")

    # The headline: compared with taking the fastest route, how much heat did
    # each group avoid, and how much extra time did it cost?
    print("\nCompared with each person's fastest route:")
    for label, low, high in (("speed-seekers   (pref < 0.33)", 0.0, 0.33),
                             ("in between", 0.33, 0.66),
                             ("comfort-seekers (pref > 0.66)", 0.66, 1.01)):
        group = [a for a in result.agents if low <= a.preference < high
                 and a.status == "arrived" and a.replans == 0 and a.baseline_exposure > 0]
        if not group:
            print(f"  {label:<30} no comparable trips")
            continue
        heat = sum(a.exposure for a in group) / sum(a.baseline_exposure for a in group)
        time = sum(a.trip_minutes for a in group) / sum(a.baseline_minutes for a in group)
        print(f"  {label:<30} heat {100 * (heat - 1):+6.1f}%   time {100 * (time - 1):+6.1f}%"
              f"   ({len(group)} people)")
    print("  (people who replanned around the closure are left out of this comparison)")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def prepare_planner(time: str, scenario: str, forecast: str | None = None,
                    dataset: Path = DEFAULT_DATASET_PATH) -> Planner:
    """Run the real pipeline once and build a planner on its result.

    forecast: override config.json's forecast_mode ("current", "oracle", "noisy").
    """
    from dataclasses import replace

    from main import run_simulation  # reuse the team's pipeline, don't copy it

    context = {"time": time, "scenario": scenario}
    with contextlib.redirect_stdout(io.StringIO()):   # keep the pipeline's prints quiet
        output = run_simulation(load_campus_graph(dataset), rounds=1, context=context)
    settings = RoutingSettings.from_file()
    if forecast:
        settings = replace(settings, forecast_mode=forecast)
    planner = Planner(output.graph, settings, context)
    rates = [planner.heat.rate(e) for e in planner.edges]
    print(f"Campus ready at {time} ({scenario}, forecast: {settings.forecast_mode}): "
          f"heat rate {min(rates):.1f}-{max(rates):.1f} C above comfortable; crosswalk waits "
          f"{sorted({round(w * 60) for w in planner.signal_wait_min.values()})} s")
    return planner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agents", type=int, default=10)
    parser.add_argument("--time", default="13:00", help='clock time, "HH:MM"')
    parser.add_argument("--scenario", default="hot")
    parser.add_argument("--block-at", type=float, default=2.0,
                        help="minute at which the busiest walkway closes (0 = never)")
    parser.add_argument("--seed", type=int, default=1, help="change for a different crowd")
    parser.add_argument("--dt", type=float, default=5.0, help="seconds per simulation step")
    parser.add_argument("--forecast", choices=["current", "oracle", "noisy"],
                        help="override config.json's forecast_mode")
    parser.add_argument("--animate", action="store_true", help="also save simulation.gif")
    parser.add_argument("--output-dir", type=Path, default=Path("routing_output"))
    args = parser.parse_args()

    planner = prepare_planner(args.time, args.scenario, args.forecast)
    result = run(planner, args.agents, args.block_at, args.seed, args.dt)
    report(result)

    if args.animate:
        from .visualize import animate

        path = args.output_dir / "simulation.gif"
        print(f"\nRendering {len(result.frames)} frames...")
        animate(result, path)


if __name__ == "__main__":
    main()
