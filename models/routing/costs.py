"""How much walking an edge "costs", and where the heat numbers come from.

Three ideas live here:

1. A HEAT SOURCE answers "how hot is this edge right now?"
   - PipelineHeat reads the real Heat Exposure model's output from the graph.
   - PlaceholderHeat invents believable numbers when that output is missing.
   - OverrideHeat wraps another source and forces chosen edges to new values
     (used by the --heat-test check).
   Routing never needs to know which one it is using.

2. A DISCOMFORT function turns a heat rate into a 0..1 "how bad is it" number.

3. The STEP COST turns time + heat + the walker's preference into the one
   number Dijkstra minimizes. Lower is better.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Callable, Mapping, Protocol

from utils import CampusGraph, GraphEdge


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_SHADE_SCHEDULE = PROJECT_ROOT / "dataset" / "campus_50" / "shade_schedule.json"

# Property names written by models/heat_exposure.
RATE_PROPERTY = "heat_exposure_rate"   # UTCI degrees C above 26 C (comfortable)
UNSAFE_PROPERTY = "unsafe_heat"        # 1.0 if UTCI >= 38 C, else 0.0


# ---------------------------------------------------------------------------
# Heat sources
# ---------------------------------------------------------------------------
class HeatSource(Protocol):
    """Anything that can say how hot each edge is right now.

    `rate(edge_id)` is heat_exposure_rate: UTCI degrees C above comfortable
    (26 C). 0 means comfortable; higher means hotter. Multiplying it by minutes
    walked gives "degree-minutes" of heat exposure.
    """

    is_placeholder: bool

    def rate(self, edge_id: str) -> float: ...
    def unsafe(self, edge_id: str) -> float: ...


class PipelineHeat:
    """REAL heat: reads what the Heat Exposure model wrote onto each edge."""

    is_placeholder = False

    def __init__(self, graph: CampusGraph) -> None:
        self._rates = {e.edge_id: float(e.properties[RATE_PROPERTY]) for e in graph.edges}
        self._unsafe = {e.edge_id: float(e.properties[UNSAFE_PROPERTY]) for e in graph.edges}

    @staticmethod
    def available(graph: CampusGraph) -> bool:
        """True if every edge already has the heat model's output on it."""
        return all(
            RATE_PROPERTY in e.properties and UNSAFE_PROPERTY in e.properties
            for e in graph.edges
        )

    def rate(self, edge_id: str) -> float:
        return self._rates[edge_id]

    def unsafe(self, edge_id: str) -> float:
        return self._unsafe[edge_id]


class PlaceholderHeat:
    """FAKE but believable heat, for when the real heat model's output is missing.

    It leans on the dataset's own shade schedule, so the fake numbers follow the
    campus layout: open plazas are hot, tree-lined paths are cooler, and paths
    beside buildings change through the day as the shadow moves. Small seeded
    noise keeps edges with the same shade from being identical.
    """

    is_placeholder = True
    HOTTEST_RATE = 15.0   # about the hottest the real model gives on a hot afternoon
    SHADE_RELIEF = 0.6    # full shade removes 60% of the heat (the air is still hot)
    UNSAFE_RATE = 12.0    # matches the real model's unsafe threshold (38 - 26)

    def __init__(
        self,
        graph: CampusGraph,
        time_text: str = "13:00",
        seed: int = 42,
        shade_schedule_path: Path = DEFAULT_SHADE_SCHEDULE,
    ) -> None:
        print("WARNING: using PLACEHOLDER heat values (fake data, not the heat model).")
        self._rates: dict[str, float] = {}
        shade = _load_shade(shade_schedule_path, time_text)
        for edge in graph.edges:
            # Each edge gets its own random generator, seeded from its ID, so the
            # values are the same every run and don't depend on edge order.
            noise = random.Random(f"{seed}-{edge.edge_id}").uniform(-0.8, 0.8)
            if shade is not None and edge.edge_id in shade:
                shade_fraction = shade[edge.edge_id] / 100.0
                base = self.HOTTEST_RATE * (1 - self.SHADE_RELIEF * shade_fraction)
            else:
                # No shade schedule for this dataset: smooth hot/cool patches
                # based on where the middle of the edge is.
                a, b = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
                mx, my = (a.x_m + b.x_m) / 2, (a.y_m + b.y_m) / 2
                base = 11.0 + 3.5 * math.sin(mx / 90) * math.cos(my / 70)
            self._rates[edge.edge_id] = max(0.0, round(base + noise, 2))

    def rate(self, edge_id: str) -> float:
        return self._rates[edge_id]

    def unsafe(self, edge_id: str) -> float:
        return 1.0 if self._rates[edge_id] >= self.UNSAFE_RATE else 0.0


class OverrideHeat:
    """Another heat source, with some edges forced to a chosen rate.

    Used by the heat test: "pretend the fastest route is scorching and see
    whether comfort-seekers avoid it".
    """

    def __init__(self, base: HeatSource, overrides: Mapping[str, float], unsafe_rate: float = 12.0):
        self._base = base
        self._overrides = dict(overrides)
        self._unsafe_rate = unsafe_rate
        self.is_placeholder = base.is_placeholder

    def rate(self, edge_id: str) -> float:
        return self._overrides.get(edge_id, self._base.rate(edge_id))

    def unsafe(self, edge_id: str) -> float:
        if edge_id in self._overrides:
            return 1.0 if self._overrides[edge_id] >= self._unsafe_rate else 0.0
        return self._base.unsafe(edge_id)


def choose_heat_source(graph: CampusGraph, wanted: str, time_text: str, seed: int) -> HeatSource:
    """Pick the heat source named in config, falling back to placeholders if needed."""
    if wanted not in {"pipeline", "placeholder"}:
        raise ValueError(f"heat_source must be 'pipeline' or 'placeholder', got '{wanted}'")
    if wanted == "pipeline" and PipelineHeat.available(graph):
        return PipelineHeat(graph)
    if wanted == "pipeline":
        print("NOTE: heat model output not found on the graph; falling back to placeholders.")
    return PlaceholderHeat(graph, time_text=time_text, seed=seed)


def _load_shade(path: Path, time_text: str) -> dict[str, float] | None:
    """Shade percent per edge at the nearest 30-minute slot, or None if unavailable."""
    try:
        schedule = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    wanted = clock_minutes(time_text)
    # Pick the schedule's time slot closest to the requested time.
    slot = min(schedule["time_slots"], key=lambda s: abs(clock_minutes(s) - wanted))
    return {
        edge_id: float(schedule["profiles"][profile][slot])
        for edge_id, profile in schedule["edges"].items()
    }


def clock_minutes(time_text: str) -> int:
    """"13:30" -> 810 (minutes after midnight)."""
    hours, minutes = time_text.split(":")
    return int(hours) * 60 + int(minutes)


# ---------------------------------------------------------------------------
# Discomfort: heat rate -> 0..1
# ---------------------------------------------------------------------------
def make_discomfort(
    mode: str, scale_c: float, heat: HeatSource, edge_ids: list[str]
) -> Callable[[float], float]:
    """Build the function that turns a heat rate into "how bad is it", 0..1.

    "absolute" (default): rate / scale_c, capped at 1. With scale_c = 20, an
        edge scores 1 at UTCI 46 C, where the official UTCI scale starts
        "extreme heat stress" (26 C comfortable + 20). A smaller scale such as
        12 (the 38 C unsafe line) saturates on hot afternoons: most edges score
        1 and look equally bad, so comfort routing stops working.
    "relative": where the edge sits between today's coolest and hottest edge.
        Measured on campus_50 this backfires: it treats small heat differences
        as huge, sending people on long detours that add more exposure (extra
        minutes in the heat) than they save. Kept for experiments (see README).
    """
    if mode == "absolute":
        return lambda rate: min(1.0, max(0.0, rate / scale_c))
    if mode == "relative":
        rates = [heat.rate(e) for e in edge_ids]
        low, high = min(rates), max(rates)
        span = high - low
        if span <= 0:  # every edge equally hot: nothing to prefer
            return lambda rate: 0.0
        return lambda rate: min(1.0, max(0.0, (rate - low) / span))
    raise ValueError(f"discomfort_mode must be 'absolute' or 'relative', got '{mode}'")


# ---------------------------------------------------------------------------
# The step cost (what Dijkstra adds up)
# ---------------------------------------------------------------------------
def make_step_cost(
    rate_at: Callable[[str, float], float],
    unsafe: Callable[[str], float],
    discomfort: Callable[[float], float],
    preference: float,
    *,
    k: float,
    unsafe_penalty_min: float = 0.0,
    signal_wait_min: Mapping[str, float] | None = None,
    targets: frozenset[str] = frozenset(),
    start_min: float = 0.0,
    crowd: Mapping[str, float] | None = None,
):
    """Build the step-cost function Dijkstra calls for each edge it considers.

    preference: 0 = fastest route, 1 = most comfortable route.

        minutes = walk_time_min (+ crosswalk wait if we walk INTO a signal)
        cost    = minutes * (1 + k * preference * discomfort)
                + unsafe_penalty_min * preference * unsafe_heat

    - preference 0 -> the heat terms vanish -> plain fastest route.
    - Time is NEVER dropped, even at preference 1, so a shaded edge still costs
      something and the router won't send people on endless shaded detours.
    - At preference 1, the worst edge "feels" (1 + k) times as long as it is.

    rate_at(edge_id, minutes_from_now) gives the heat rate when the walker gets
    there. Without a forecast it simply ignores the time (see forecast.py).
    start_min shifts "now", for walkers who replan partway through a simulation.
    """
    if not 0.0 <= preference <= 1.0:
        raise ValueError(f"preference must be between 0 and 1, got {preference}")
    waits = signal_wait_min or {}

    def step_cost(edge: GraphEdge, from_node: str, to_node: str, elapsed_min: float):
        minutes = edge.walk_time_min
        # Walking INTO a signalized crossing means waiting there, unless that
        # node is where we're going (then we've arrived and don't cross).
        if to_node in waits and to_node not in targets:
            minutes += waits[to_node]

        bad = discomfort(rate_at(edge.edge_id, start_min + elapsed_min))
        cost = minutes * (1 + k * preference * bad)
        cost += unsafe_penalty_min * preference * unsafe(edge.edge_id)

        # FUTURE (crowd density, not built yet): busy walkways could cost more.
        # `crowd` is accepted already so callers can start passing it; the
        # term would go here, for example:
        #     cost *= 1 + crowd_weight * (crowd or {}).get(edge.edge_id, 0.0)
        # The routing model already counts `planned_pedestrians` per edge,
        # which is what a density term would read.
        return cost, minutes

    return step_cost
