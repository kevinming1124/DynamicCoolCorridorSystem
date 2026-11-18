"""How much walking an edge "costs", and where the heat numbers come from.

Two ideas live here:

1. A HEAT SOURCE answers "how hot is this edge?" There's a real one, which reads
   the Heat Exposure model's output from the graph, and a placeholder that
   invents believable numbers when that output isn't there. Routing never needs
   to know which one it's using.

2. The STEP COST turns time + heat + the walker's preference into one number
   that Dijkstra minimizes. Lower is better.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Protocol

from utils import CampusGraph, GraphEdge


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_SHADE_SCHEDULE = PROJECT_ROOT / "dataset" / "campus_50" / "shade_schedule.json"

# Default tuning values. These move to config.json when the model is built (step 4).
COMFORT_WEIGHT_K = 4.0       # at preference 1, the worst edge "feels" (1 + K) times as long
DISCOMFORT_SCALE_C = 12.0    # degrees above comfortable that count as fully uncomfortable
                             # (12 = the gap between comfortable 26 C and unsafe 38 C UTCI)


# ---------------------------------------------------------------------------
# Heat sources
# ---------------------------------------------------------------------------
class HeatSource(Protocol):
    """Anything that can say how hot each edge is.

    `rate` is heat_exposure_rate: UTCI degrees C above comfortable (26 C), per
    minute walked. 0 means comfortable; higher means hotter.
    """

    def rate(self, edge_id: str) -> float: ...
    def unsafe(self, edge_id: str) -> float: ...


class PlaceholderHeat:
    """FAKE but believable heat, for when the real heat model's output is missing.

    It leans on the dataset's own shade schedule, so the fake numbers follow the
    campus layout: open plazas are hot, tree-lined paths are cooler, and paths
    beside buildings change through the day as the shadow moves. Small seeded
    noise keeps edges with the same shade from being identical.
    """

    HOTTEST_RATE = 15.0   # roughly the hottest the real model gives on a hot afternoon
    SHADE_RELIEF = 0.6    # full shade removes 60% of the heat (air is still hot)
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
            if shade is not None:
                shade_fraction = shade.get(edge.edge_id, 5.0) / 100.0
                base = self.HOTTEST_RATE * (1 - self.SHADE_RELIEF * shade_fraction)
            else:
                # No shade schedule (another dataset): smooth hot/cool patches
                # based on where the edge's middle is.
                a, b = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
                mx, my = (a.x_m + b.x_m) / 2, (a.y_m + b.y_m) / 2
                base = 11.0 + 3.5 * math.sin(mx / 90) * math.cos(my / 70)
            self._rates[edge.edge_id] = max(0.0, round(base + noise, 2))

    def rate(self, edge_id: str) -> float:
        return self._rates[edge_id]

    def unsafe(self, edge_id: str) -> float:
        return 1.0 if self._rates[edge_id] >= self.UNSAFE_RATE else 0.0


def _load_shade(path: Path, time_text: str) -> dict[str, float] | None:
    """Shade percent per edge at the nearest 30-minute slot, or None if unavailable."""
    try:
        schedule = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    hours, minutes = (int(part) for part in time_text.split(":"))
    wanted = hours * 60 + minutes
    # Pick the schedule's time slot closest to the requested time.
    slot = min(
        schedule["time_slots"],
        key=lambda s: abs(int(s[:2]) * 60 + int(s[3:]) - wanted),
    )
    return {
        edge_id: float(schedule["profiles"][profile][slot])
        for edge_id, profile in schedule["edges"].items()
    }


# ---------------------------------------------------------------------------
# The cost formula
# ---------------------------------------------------------------------------
def discomfort(rate: float, scale_c: float = DISCOMFORT_SCALE_C) -> float:
    """Heat rate -> 0..1 "how bad is it". 0 = comfortable, 1 = at/above unsafe."""
    return min(1.0, max(0.0, rate / scale_c))


def make_step_cost(
    heat: HeatSource,
    preference: float,
    k: float = COMFORT_WEIGHT_K,
    scale_c: float = DISCOMFORT_SCALE_C,
):
    """Build the step-cost function Dijkstra calls for each edge it considers.

    preference: 0 = fastest route, 1 = most comfortable route.

        cost = minutes * (1 + k * preference * discomfort)

    - preference 0 -> the heat term vanishes -> plain fastest route.
    - Time is NEVER dropped, even at preference 1, so a shaded edge still costs
      something and the router won't send people on endless shaded detours.
    - At preference 1, the worst edge "feels" (1 + k) times as long as it is.
    """
    if not 0.0 <= preference <= 1.0:
        raise ValueError(f"preference must be between 0 and 1, got {preference}")

    def step_cost(edge: GraphEdge, from_node: str, to_node: str, elapsed_min: float):
        minutes = edge.walk_time_min
        bad = discomfort(heat.rate(edge.edge_id), scale_c)
        cost = minutes * (1 + k * preference * bad)
        return cost, minutes

    return step_cost
