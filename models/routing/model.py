"""Routing model: comfort-vs-speed walking routes on the campus graph.

Where it sits in the pipeline (see main.py):

    each round:  Control -> Environment -> Heat Exposure -> Routing

It reads the heat the earlier stages put on every edge, the crosswalk wait
times the Control model set on traffic signals, and the list of closed
walkways, then plans routes. Two things come out:

- On every edge (pipeline properties):
    blocked              1.0 if the walkway is closed this round, else 0.0
    planned_pedestrians  how many of this round's planned routes use the edge
- In diagnostics (visible with --debug-dir): every planned route and its stats.

Other code (the simulation, the pictures) uses the `Planner` class directly:

    planner = Planner(graph, settings, context)
    planned = planner.route("Leavey Library", "Seeley G. Mudd Building", preference=1.0)

Run the self-checks and demo from the repository root:

    python -m models.routing.model
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph

from .costs import HeatSource, choose_heat_source, make_discomfort, make_step_cost
from .dijkstra import RouteResult, build_adjacency, dijkstra, resolve_nodes
from .forecast import make_forecast


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_DATASET_PATH = PROJECT_ROOT / "dataset" / "campus_50"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoutingSettings:
    """Every tunable value, loaded and checked from config.json."""

    model_name: str
    description: str
    enabled: bool
    output_properties: tuple[str, ...]
    comfort_weight_k: float
    discomfort_scale_c: float
    unsafe_penalty_min: float
    random_trips: int
    seed: int
    forecast_noise_c_per_hour: float
    heat_source: str
    forecast_mode: str
    discomfort_mode: str
    blocked_edges: tuple[str, ...]
    demo_trips: tuple[Mapping[str, Any], ...]
    assign_to_simulator: bool = True

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "RoutingSettings":
        config_path = Path(path)
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {config_path} at line {exc.lineno}, column {exc.colno}"
            ) from exc

        required = {"model_name", "description", "enabled", "property_target",
                    "output_properties", "input_properties", "parameters", "metadata"}
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"Missing configuration fields: {', '.join(sorted(missing))}")
        if raw["property_target"] != "edge":
            raise ValueError("The routing model must target edges")
        outputs = tuple(raw["output_properties"])
        if set(outputs) != {"blocked", "planned_pedestrians"}:
            raise ValueError("'output_properties' must be 'blocked' and 'planned_pedestrians'")

        params = raw["parameters"]
        for name, value in params.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                raise ValueError(f"Parameter '{name}' must be a finite number")
        meta = raw["metadata"]

        settings = cls(
            model_name=str(raw["model_name"]).strip(),
            description=str(raw["description"]),
            enabled=bool(raw["enabled"]),
            output_properties=outputs,
            comfort_weight_k=float(params["comfort_weight_k"]),
            discomfort_scale_c=float(params["discomfort_scale_c"]),
            unsafe_penalty_min=float(params["unsafe_penalty_min"]),
            random_trips=int(params["random_trips"]),
            seed=int(params["seed"]),
            forecast_noise_c_per_hour=float(params["forecast_noise_c_per_hour"]),
            heat_source=str(meta.get("heat_source", "pipeline")),
            forecast_mode=str(meta.get("forecast_mode", "current")),
            discomfort_mode=str(meta.get("discomfort_mode", "absolute")),
            blocked_edges=tuple(meta.get("blocked_edges", [])),
            demo_trips=tuple(meta.get("demo_trips", [])),
            assign_to_simulator=bool(meta.get("assign_to_simulator", True)),
        )
        settings._check_numbers()
        return settings

    def _check_numbers(self) -> None:
        if self.comfort_weight_k < 0 or self.unsafe_penalty_min < 0:
            raise ValueError("comfort_weight_k and unsafe_penalty_min must be >= 0")
        if self.discomfort_scale_c <= 0:
            raise ValueError("discomfort_scale_c must be > 0")
        if self.random_trips < 0 or self.forecast_noise_c_per_hour < 0:
            raise ValueError("random_trips and forecast_noise_c_per_hour must be >= 0")
        for trip in self.demo_trips:
            if not {"start", "destination", "preference"} <= set(trip):
                raise ValueError(f"demo trip needs start, destination, preference: {trip}")
            if not 0.0 <= float(trip["preference"]) <= 1.0:
                raise ValueError(f"demo trip preference must be in [0, 1]: {trip}")


# ---------------------------------------------------------------------------
# Route statistics
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteStats:
    """What a route means for the person walking it (unlike Dijkstra's cost)."""

    distance_m: float
    minutes: float            # walking + crosswalk waits
    exposure: float           # heat exposure, degree-minutes (lower is better)
    signal_wait_min: float
    signals_crossed: int


@dataclass(frozen=True)
class PlannedRoute:
    start: str
    destination: str
    preference: float
    route: RouteResult
    stats: RouteStats

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form, for diagnostics and debug files."""
        return {
            "start": self.start,
            "destination": self.destination,
            "preference": self.preference,
            "nodes": self.route.nodes,
            "edges": [step.edge_id for step in self.route.steps],
            "distance_m": round(self.stats.distance_m, 1),
            "minutes": round(self.stats.minutes, 2),
            "heat_exposure_degree_min": round(self.stats.exposure, 2),
            "signal_wait_min": round(self.stats.signal_wait_min, 2),
            "signals_crossed": self.stats.signals_crossed,
        }


# ---------------------------------------------------------------------------
# The planner: everything needed to answer route requests on one graph
# ---------------------------------------------------------------------------
class Planner:
    """Plans routes on one graph state (one round's heat, signals, closures).

    Building it once and asking many questions avoids redoing the setup
    (adjacency list, heat lookup, signal waits) for every route.
    """

    def __init__(
        self,
        graph: CampusGraph,
        settings: RoutingSettings,
        context: Mapping[str, str] | None = None,
        heat: HeatSource | None = None,
    ) -> None:
        self.graph = graph
        self.settings = settings
        self.context = dict(context or {})
        self.adjacency = build_adjacency(graph)
        self.edges = {edge.edge_id: edge for edge in graph.edges}

        self.heat = heat or choose_heat_source(
            graph, settings.heat_source, self.context.get("time", "13:00"), settings.seed
        )
        self.forecast = make_forecast(
            settings.forecast_mode, graph, self.context, self.heat,
            settings.forecast_noise_c_per_hour, settings.seed,
        )
        self.discomfort = make_discomfort(
            settings.discomfort_mode, settings.discomfort_scale_c, self.heat, list(self.edges)
        )

        # Crosswalk waits come from the Control model's live settings, so when
        # it shortens waits at hot crossings, routes respond automatically.
        self.signal_wait_min = {
            a.anchor_node: float(a.properties.get("wait_time_s") or 0.0) / 60.0
            for a in graph.actuators.values()
            if a.actuator_type == "traffic_signal"
        }

        # Closed walkways: from config, plus any edge an earlier round marked.
        self.blocked: set[str] = set(settings.blocked_edges)
        self.blocked |= {e.edge_id for e in graph.edges if e.properties.get("blocked") == 1.0}
        unknown = self.blocked - set(self.edges)
        if unknown:
            raise ValueError(f"blocked_edges contains unknown edge IDs: {sorted(unknown)}")

    # -- closing walkways ---------------------------------------------------
    def block(self, edge_id: str) -> None:
        if edge_id not in self.edges:
            raise ValueError(f"Unknown edge: {edge_id}")
        self.blocked.add(edge_id)

    # -- planning -----------------------------------------------------------
    def route(self, start: str, destination: str, preference: float) -> PlannedRoute | None:
        """Plan by place name or node ID. Returns None if no route exists."""
        start_nodes = resolve_nodes(self.graph, start)
        targets = resolve_nodes(self.graph, destination)
        # A start "building" with several entrances: leave from the first one.
        # (A real-campus version could try them all and keep the best.)
        start_node = sorted(start_nodes)[0]
        result = self.route_nodes(start_node, targets, preference)
        if result is None:
            return None
        return PlannedRoute(start, destination, preference, result, self.stats(result, targets))

    def route_nodes(
        self, start_node: str, targets: set[str], preference: float, start_min: float = 0.0
    ) -> RouteResult | None:
        """Plan between node IDs. start_min = minutes since 'now' (for replanning)."""
        step_cost = make_step_cost(
            self.forecast.rate_at,
            self.heat.unsafe,
            self.discomfort,
            preference,
            k=self.settings.comfort_weight_k,
            unsafe_penalty_min=self.settings.unsafe_penalty_min,
            signal_wait_min=self.signal_wait_min,
            targets=frozenset(targets),
            start_min=start_min,
        )
        return dijkstra(self.adjacency, start_node, targets, step_cost, frozenset(self.blocked))

    def stats(self, result: RouteResult, targets: set[str]) -> RouteStats:
        """Distance, time, and heat exposure a walker actually experiences.

        Exposure = heat rate x minutes on each edge, plus heat rate x minutes
        spent waiting at each crossing (using the edge you arrived on).
        """
        distance = minutes = exposure = waited = 0.0
        crossed = 0
        for step in result.steps:
            edge = self.edges[step.edge_id]
            rate = self.heat.rate(step.edge_id)
            distance += edge.distance_m
            minutes += edge.walk_time_min
            exposure += rate * edge.walk_time_min
            wait = self.signal_wait_min.get(step.to_node, 0.0)
            if wait and step.to_node not in targets:
                minutes += wait
                exposure += rate * wait
                waited += wait
                crossed += 1
        return RouteStats(distance, minutes, exposure, waited, crossed)

    def named_places(self) -> list[str]:
        return sorted({n.location_name for n in self.graph.nodes.values() if n.location_name})


# ---------------------------------------------------------------------------
# The pipeline stage
# ---------------------------------------------------------------------------
class RoutingModel:
    """Pipeline stage: plan this round's trips and report per-edge results.

    If given the pedestrian simulator (models/pedestrian, Aldo's), it also
    hands every route it plans to that simulator as a new pedestrian, using the
    simulator's `assign_route`. Routing decides WHERE people walk; the
    simulator moves them. In main.py the order each round is:

        Control -> Environment -> Heat Exposure -> Routing -> Pedestrian
    """

    def __init__(self, settings: RoutingSettings, simulator: Any = None) -> None:
        self.settings = settings
        self.simulator = simulator   # a models.pedestrian PedestrianSimulator, or None
        self._round = 0              # how many times predict() has run

    @classmethod
    def from_config(
        cls, path: str | Path = DEFAULT_CONFIG_PATH, simulator: Any = None
    ) -> "RoutingModel":
        return cls(RoutingSettings.from_file(path), simulator)

    def planner(self, graph: CampusGraph, context: Mapping[str, str] | None = None) -> Planner:
        return Planner(graph, self.settings, context)

    def predict(self, model_input: ModelInput) -> ModelOutput:
        if not self.settings.enabled:
            raise RuntimeError(f"Model '{self.settings.model_name}' is disabled")

        self._round += 1
        graph = model_input.graph
        planner = self.planner(graph, model_input.context)
        # Fixed demo trips only in the first round; fresh random trips every
        # round (the seed shifts per round, so each round brings new people).
        demo = list(self.settings.demo_trips) if self._round == 1 else []
        trips = demo + random_trips(
            planner.named_places(), self.settings.random_trips,
            self.settings.seed + self._round - 1,
        )

        planned: list[PlannedRoute] = []
        unreachable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        known = set(planner.named_places()) | set(graph.nodes)
        for trip in trips:
            # demo_trips name campus_50 places. On another dataset (e.g.
            # tiny_campus_block) those places don't exist: skip, don't crash.
            if trip["start"] not in known or trip["destination"] not in known:
                skipped.append(dict(trip))
                continue
            result = planner.route(trip["start"], trip["destination"], float(trip["preference"]))
            if result is None:
                unreachable.append(dict(trip))
            else:
                planned.append(result)

        # Per-edge outputs: one value for EVERY edge, as the pipeline requires.
        usage = {edge_id: 0.0 for edge_id in planner.edges}
        for p in planned:
            for step in p.route.steps:
                usage[step.edge_id] += 1.0
        blocked = {edge_id: (1.0 if edge_id in planner.blocked else 0.0) for edge_id in planner.edges}
        values = {"blocked": blocked, "planned_pedestrians": usage}
        new_graph = graph
        for name in self.settings.output_properties:
            new_graph = new_graph.with_edge_property(name, values[name])

        assigned = self._hand_to_simulator(planned)

        rates = [planner.heat.rate(e) for e in planner.edges]
        effect = comfort_effect(planner)
        _print_summary(planned, unreachable, rates, effect, planner.heat.is_placeholder)
        if assigned:
            print(f"  handed {len(assigned)} routes to the pedestrian simulator "
                  f"(pedestrian IDs {assigned[0]}-{assigned[-1]})")
        if skipped:
            print(f"  (skipped {len(skipped)} demo trip(s) naming places not in this dataset)")

        return ModelOutput(
            model_name=self.settings.model_name,
            property_target="edge",
            property_names=self.settings.output_properties,
            property_values={name: values[name] for name in self.settings.output_properties},
            graph=new_graph,
            diagnostics={
                "heat_source": "placeholder" if planner.heat.is_placeholder else "pipeline",
                "forecast_mode": self.settings.forecast_mode,
                "discomfort_mode": self.settings.discomfort_mode,
                "heat_rate_range": [round(min(rates), 2), round(max(rates), 2)],
                "comfort_changes_route_fraction": effect,
                "blocked_edges": sorted(planner.blocked),
                "routes": [p.as_dict() for p in planned],
                "unreachable_trips": unreachable,
                "skipped_trips": skipped,
                "assigned_pedestrian_ids": assigned,
            },
        )

    def _hand_to_simulator(self, planned: list[PlannedRoute]) -> list[int]:
        """Give each planned route to the pedestrian simulator as a new person.

        New IDs continue after the largest ID already in the simulator, so they
        never clash with people loaded from a --routes file or earlier rounds.
        Returns the IDs assigned.
        """
        if self.simulator is None or not self.settings.assign_to_simulator:
            return []
        next_id = max(self.simulator.pedestrians, default=0) + 1
        assigned = []
        for p in planned:
            nodes = p.route.nodes
            if len(nodes) < 2:   # already at the destination: nothing to walk
                continue
            self.simulator.assign_route(next_id, nodes)
            assigned.append(next_id)
            next_id += 1
        return assigned


def random_trips(places: list[str], count: int, seed: int) -> list[dict[str, Any]]:
    """`count` random trips between two different named places, repeatable via seed."""
    rng = random.Random(seed)
    trips = []
    for _ in range(count):
        start, destination = rng.sample(places, 2)
        trips.append({"start": start, "destination": destination,
                      "preference": round(rng.random(), 2)})
    return trips


def comfort_effect(planner: Planner, pairs: int = 40, seed: int = 7) -> float:
    """Fraction of random trips where the comfort route differs from the fastest.

    A health check on the heat data: if this is near 0, the heat values are too
    similar across edges for comfort routing to matter (see README).
    """
    places = planner.named_places()
    rng = random.Random(seed)
    changed = total = 0
    for _ in range(pairs):
        start, destination = rng.sample(places, 2)
        fast = planner.route(start, destination, 0.0)
        cool = planner.route(start, destination, 1.0)
        if fast is None or cool is None:
            continue
        total += 1
        changed += fast.route.nodes != cool.route.nodes
    return round(changed / total, 2) if total else 0.0


def _print_summary(planned, unreachable, rates, effect, placeholder) -> None:
    source = "PLACEHOLDER heat" if placeholder else "heat model"
    print(f"Routing ({source}): heat rate across edges {min(rates):.1f}-{max(rates):.1f} "
          f"C above comfortable; comfort changes the route on {effect:.0%} of sample trips")
    print(f"  {'trip':<52}{'pref':>5}{'min':>7}{'exposure':>10}{'signals':>8}")
    for p in planned:
        trip = f"{p.start[:24]} -> {p.destination[:24]}"
        print(f"  {trip:<52}{p.preference:>5.2f}{p.stats.minutes:>7.1f}"
              f"{p.stats.exposure:>10.1f}{p.stats.signals_crossed:>8}")
    for trip in unreachable:
        print(f"  {trip['start']} -> {trip['destination']}: NO ROUTE (blocked off)")


# ---------------------------------------------------------------------------
# Self-checks and demo: python -m models.routing.model
# ---------------------------------------------------------------------------
def _self_checks() -> None:
    """Acceptance checks from ROUTING_HANDOFF.md section 11, on placeholder heat."""
    from dataclasses import replace

    from .costs import OverrideHeat
    from .dijkstra import _check_against_floyd_warshall

    graph = load_campus_graph(DEFAULT_DATASET_PATH)
    settings = replace(RoutingSettings.from_file(), heat_source="placeholder")
    start, destination = "Leavey Library", "Seeley G. Mudd Building"

    print("1. Dijkstra correctness")
    _check_against_floyd_warshall(graph)

    print("\n2. Comfort works (heat test)")
    base = Planner(graph, settings, {"time": "13:00"})
    fast = base.route(start, destination, 0.0)
    scorching = OverrideHeat(base.heat, {s.edge_id: 25.0 for s in fast.route.steps})
    tested = Planner(graph, settings, {"time": "13:00"}, heat=scorching)
    hot_fast = tested.route(start, destination, 0.0)
    hot_cool = tested.route(start, destination, 1.0)
    assert hot_fast.route.nodes == fast.route.nodes, "speed route should ignore heat"
    assert hot_cool.route.nodes != hot_fast.route.nodes, "comfort route should avoid the heat"
    assert hot_cool.stats.exposure < hot_fast.stats.exposure
    print(f"   fastest route made scorching: preference 0 keeps it "
          f"({hot_fast.stats.minutes:.1f} min, exposure {hot_fast.stats.exposure:.0f}); "
          f"preference 1 avoids it ({hot_cool.stats.minutes:.1f} min, "
          f"exposure {hot_cool.stats.exposure:.0f})")

    print("\n3. Blocking works")
    blocked_planner = Planner(graph, settings, {"time": "13:00"}, heat=base.heat)
    first_edge = fast.route.steps[0].edge_id
    blocked_planner.block(first_edge)
    detour = blocked_planner.route(start, destination, 0.0)
    assert detour is not None and first_edge not in {s.edge_id for s in detour.route.steps}
    print(f"   blocked {first_edge}: route changed, {fast.stats.minutes:.1f} -> "
          f"{detour.stats.minutes:.1f} min")
    target = sorted(resolve_nodes(graph, destination))[0]
    for _, edge in blocked_planner.adjacency[target]:
        blocked_planner.block(edge.edge_id)
    assert blocked_planner.route(start, destination, 0.0) is None
    print("   blocked every walkway into the destination: no route, no crash")

    print("\n4. Signals")
    through_signal = None
    for place_a in base.named_places():
        for place_b in base.named_places():
            p = place_a != place_b and base.route(place_a, place_b, 0.0)
            if p and p.stats.signals_crossed:
                through_signal = (place_a, place_b, p)
                break
        if through_signal:
            break
    a, b, slow = through_signal
    shorter = graph.with_actuator_property("wait_time_s", {
        act_id: (act.min_wait_time_s if act.actuator_type == "traffic_signal" else None)
        for act_id, act in graph.actuators.items()
    })
    quick = Planner(shorter, settings, {"time": "13:00"}, heat=base.heat).route(a, b, 0.0)
    assert quick.stats.minutes < slow.stats.minutes
    print(f"   {a} -> {b}: default waits {slow.stats.minutes:.1f} min, "
          f"shortest waits {quick.stats.minutes:.1f} min")

    print("\n5. Forecast 'current' changes nothing")
    # Same formula, but reading heat straight from the source, with no forecast.
    def no_forecast(edge_id: str, minutes_ahead: float) -> float:
        return base.heat.rate(edge_id)

    plain_step = make_step_cost(no_forecast, base.heat.unsafe, base.discomfort, 1.0,
                                k=settings.comfort_weight_k,
                                signal_wait_min=base.signal_wait_min,
                                targets=frozenset(resolve_nodes(graph, destination)))
    plain = dijkstra(base.adjacency, sorted(resolve_nodes(graph, start))[0],
                     resolve_nodes(graph, destination), plain_step)
    assert plain.nodes == base.route(start, destination, 1.0).route.nodes
    print("   identical routes with and without the forecast layer")
    print("\nAll checks passed.\n")


if __name__ == "__main__":
    _self_checks()
    print("Demo: routing stage on placeholder heat\n")
    from dataclasses import replace

    model = RoutingModel(replace(RoutingSettings.from_file(), heat_source="placeholder"))
    output = model.predict(ModelInput(graph=load_campus_graph(DEFAULT_DATASET_PATH),
                                      context={"time": "13:00"}))
    print(f"\nWrote {', '.join(output.property_names)} on {len(output.graph.edges)} edges.")
