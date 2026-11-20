"""Render showcase GIFs from real pipeline runs on the USC map.

From the repository root (needs tools/requirements.txt and Java/JAVA_HOME):

    python tools/make_animations.py all
    python tools/make_animations.py day trips misting --out docs/animations

GIFs:
  day           warm day 08:00-18:00, 4 misters: heat spreading and fading,
                misters and signals switching (Environment, Heat, Control)
  trips         hot day from 11:00, 10 pedestrians: safe routes, coolest
                fallbacks, replanning and Verification (Routing, Pedestrian)
  misting       hot day 10:00-17:00 with 10 misters, plus the same day with
                no misters, each with a close-up of one mister zone

Each run records a small snapshot per round through run_simulation's
on_round hook; no debug files are written.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation, PillowWriter  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402

from main import build_time_schedule, load_simulation_graph, minutes_from_text, run_simulation  # noqa: E402
from models.routing import load_trip_requests  # noqa: E402

USC = ROOT / "dataset" / "usc_osm_trimmed"
DEFAULT_OUT = ROOT / "docs" / "animations"
TRIPS_FILE = ROOT / "models" / "routing" / "example_ten_trips.json"

HEAT_CMAP = LinearSegmentedColormap.from_list(
    "heat", ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027", "#7f0000"])
HEAT_NORM = Normalize(vmin=26.0, vmax=42.0)
SAFE_COLOR, FALLBACK_COLOR = "#1b7837", "#e08214"
MIST_ON, MIST_OFF = "#2171b5", "#bdbdbd"


# -- recording -------------------------------------------------------------------
@dataclass
class Frame:
    time: str
    round_number: int
    utci: list[float]
    unsafe: list[float]
    mist_cooling: list[float]
    misters: dict[str, str]
    signals: dict[str, float]
    pedestrians: list[dict]
    verification: str
    replanned: list[dict] = field(default_factory=list)


class Recorder:
    """on_round callback that keeps one light snapshot every ``every`` rounds."""

    def __init__(self, graph, every: int = 1):
        self.edge_ids = [edge.edge_id for edge in graph.edges]
        self.every = every
        self.frames: list[Frame] = []
        self.policies: dict[int, str] = {}
        self._replans: list[dict] = []

    def __call__(self, round_number, time, output, reports):
        routing = reports.get("routingc_model", {})
        for plan in routing.get("routes", []):
            self.policies[plan["pedestrian_id"]] = plan.get("route_policy", "safe_route")
        for change in routing.get("replanned_routes", []):
            self.policies[change["pedestrian_id"]] = change["route_policy"]
        self._replans.extend(routing.get("replanned_routes", []))
        if round_number % self.every:
            return
        edges = {edge.edge_id: edge for edge in output.graph.edges}
        props = [edges[e].properties for e in self.edge_ids]
        actuators = output.graph.actuators.values()
        state = output.diagnostics["pedestrian_state"]
        self.frames.append(Frame(
            time=time or "", round_number=round_number,
            utci=[p["utci_c"] for p in props], unsafe=[p["unsafe_heat"] for p in props],
            mist_cooling=[p["mist_cooling_f"] for p in props],
            misters={a.actuator_id: a.properties.get("status") for a in actuators
                     if a.actuator_type == "misting_point"},
            signals={a.actuator_id: a.properties.get("wait_time_s") for a in actuators
                     if a.actuator_type == "traffic_signal"},
            pedestrians=[{**p, "policy": self.policies.get(p["pedestrian_id"], "safe_route")}
                         for p in state["pedestrians"]],
            verification=output.diagnostics["verification"]["status"],
            replanned=self._replans))
        self._replans = []


def simulate(misting_count, scenario, recorder_every=1, *, time_range=None, fixed_time=None,
             dt=60.0, rounds=None, trips=()):
    graph, layout, _ = load_simulation_graph(USC, misting_count)
    recorder = Recorder(graph, recorder_every)
    context = {"scenario": scenario, "dataset_dir": str(USC), "pedestrian_dt_sec": dt}
    schedule = None
    if time_range:
        start, end = (minutes_from_text(t) for t in time_range)
        schedule = build_time_schedule(start, end, dt, 30)
        rounds = rounds or len(schedule)
    else:
        context["time"] = fixed_time
    if layout is not None:
        context["misting_scenario"] = layout
    with contextlib.redirect_stdout(io.StringIO()):
        run_simulation(graph, rounds=rounds, context=context, trip_requests=list(trips),
                       dataset_directory=USC, time_schedule=schedule, on_round=recorder)
    # Label frames with the running clock at the end of each round; the
    # environment itself refreshes every 30 minutes in time-range runs.
    base = minutes_from_text(time_range[0] if time_range else fixed_time) * 60
    for frame in recorder.frames:
        seconds = base + frame.round_number * dt
        clock = f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}"
        frame.time = clock if time_range else f"{clock}:{int(seconds % 60):02d}"
    return graph, recorder.frames


# -- drawing helpers -------------------------------------------------------------
def campus_context():
    """Building outlines and green areas from map.osm, in dataset coordinates."""
    path = ROOT / "models" / "routing" / "osm_build" / "export_surroundings.py"
    spec = importlib.util.spec_from_file_location("export_surroundings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads((USC / "dataset.json").read_text(encoding="utf-8"))
    layers = module.read_osm(11, tuple(manifest["graph"]["coordinate_system"]["origin"]))
    return ([s["points"] for s in layers["building"]],
            [s["points"] for s in layers["green"] if s["kind"] == "area"])


def draw_base(ax, graph, buildings, greens, bounds):
    ax.set_facecolor("#f7f7f2")
    ax.add_collection(PolyCollection(greens, facecolor="#d9ead3", edgecolor="none", zorder=0))
    ax.add_collection(PolyCollection(buildings, facecolor="#dcdcdc", edgecolor="#c4c4c4",
                                     linewidth=0.4, zorder=0))
    segments = [[(graph.nodes[e.from_node].x_m, graph.nodes[e.from_node].y_m),
                 (graph.nodes[e.to_node].x_m, graph.nodes[e.to_node].y_m)] for e in graph.edges]
    walkways = LineCollection(segments, cmap=HEAT_CMAP, norm=HEAT_NORM, zorder=2,
                              capstyle="round")
    ax.add_collection(walkways)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    return walkways


def actuator_artists(ax, graph, show_labels=True):
    misters = {a.actuator_id: a for a in graph.actuators.values() if a.actuator_type == "misting_point"}
    signals = {a.actuator_id: a for a in graph.actuators.values() if a.actuator_type == "traffic_signal"}
    mister_dots = ax.scatter([a.x_m for a in misters.values()], [a.y_m for a in misters.values()],
                             s=70, c=MIST_OFF, edgecolors="#08306b", linewidths=0.8, zorder=5)
    ax.scatter([a.x_m for a in signals.values()], [a.y_m for a in signals.values()],
               s=46, marker="s", c="#252525", zorder=5)
    signal_text = {sid: ax.text(a.x_m + 9, a.y_m + 9, "", fontsize=7, zorder=6,
                                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#252525", lw=0.5))
                   for sid, a in signals.items()}
    if show_labels:
        for mid, a in misters.items():
            ax.text(a.x_m + 8, a.y_m - 16, mid.replace("SIM_", ""), fontsize=6.5, color="#08306b", zorder=6)
    return list(misters), mister_dots, signal_text


def bounds_of(graph, pad=25.0):
    xs = [n.x_m for n in graph.nodes.values()]
    ys = [n.y_m for n in graph.nodes.values()]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def add_colorbar(fig, ax):
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.07)
    mappable = plt.cm.ScalarMappable(norm=HEAT_NORM, cmap=HEAT_CMAP)
    bar = fig.colorbar(mappable, ax=ax, orientation="horizontal", fraction=0.04, pad=0.02, aspect=50)
    bar.set_label("Felt heat on each walkway, UTCI (°C)   —   38 °C and above is unsafe", fontsize=8)
    bar.ax.axvline(38, color="black", linewidth=1.5)
    bar.ax.tick_params(labelsize=7)


def info_box(ax):
    return ax.text(0.012, 0.985, "", transform=ax.transAxes, va="top", ha="left", fontsize=8.5,
                   family="monospace", zorder=10,
                   bbox=dict(boxstyle="round,pad=0.4", fc="white", ec="#888888", alpha=0.92))


def style_walkways(walkways, frame, base_width=1.1, unsafe_width=2.4):
    walkways.set_array(frame.utci)
    walkways.set_linewidths([unsafe_width if u else base_width for u in frame.unsafe])


def length_where(graph, values, predicate):
    return sum(e.distance_m for e, v in zip(graph.edges, values) if predicate(v))


def save(animation, path, fps):
    path.parent.mkdir(parents=True, exist_ok=True)
    animation.save(path, writer=PillowWriter(fps=fps), dpi=80)
    plt.close(animation._fig)
    print(f"Saved {path} ({path.stat().st_size / 1e6:.1f} MB)")


# -- GIF 1: a day on campus ------------------------------------------------------
def make_day(out, buildings, greens):
    graph, frames = simulate(4, "warm", time_range=("08:00", "18:00"), dt=300)
    fig, ax = plt.subplots(figsize=(10, 7.6))
    fig.suptitle("A day on campus — warm day, 4 misters", fontsize=13, weight="bold", y=0.97)
    walkways = draw_base(ax, graph, buildings, greens, bounds_of(graph))
    mister_ids, mister_dots, signal_text = actuator_artists(ax, graph)
    add_colorbar(fig, ax)
    info = info_box(ax)
    total = sum(e.distance_m for e in graph.edges)

    def update(i):
        frame = frames[i]
        style_walkways(walkways, frame)
        mister_dots.set_facecolors([MIST_ON if frame.misters[m] == "on" else MIST_OFF for m in mister_ids])
        for sid, text in signal_text.items():
            text.set_text(f"{frame.signals[sid]:g}s")
        unsafe = length_where(graph, frame.unsafe, bool) / total
        hottest = max(frame.utci)
        on = sum(s == "on" for s in frame.misters.values())
        info.set_text(f"Time          {frame.time}\n"
                      f"Unsafe paths  {100 * unsafe:4.0f}%\n"
                      f"Hottest path  {hottest:4.1f} °C\n"
                      f"Misters on    {on}/{len(frame.misters)}  (blue = on)\n"
                      f"Signal waits  {', '.join(f'{w:g}s' for w in frame.signals.values())}\n"
                      f"Verification  {frame.verification}")
        return []

    save(FuncAnimation(fig, update, frames=len(frames)), out / "1_day_on_campus.gif", fps=8)


# -- GIF 2: ten trips --------------------------------------------------------------
def pedestrian_xy(graph, lengths, person):
    route, index = person["route"], person["current_edge_index"]
    if person["state"] == "arrived" or index >= len(route) - 1:
        node = graph.nodes[route[-1]]
        return node.x_m, node.y_m
    a, b = graph.nodes[route[index]], graph.nodes[route[index + 1]]
    share = min(1.0, person["position_m"] / lengths[frozenset((a.node_id, b.node_id))])
    return a.x_m + share * (b.x_m - a.x_m), a.y_m + share * (b.y_m - a.y_m)


def remaining_segments(graph, person, start_xy):
    route, index = person["route"], person["current_edge_index"]
    points = [start_xy] + [(graph.nodes[n].x_m, graph.nodes[n].y_m) for n in route[index + 1:]]
    return list(zip(points, points[1:]))


def make_trips(out, buildings, greens):
    trips = load_trip_requests(TRIPS_FILE)
    graph, frames = simulate(4, "hot", fixed_time="11:00", dt=20, rounds=60, trips=trips)
    lengths = {frozenset((e.from_node, e.to_node)): e.distance_m for e in graph.edges}
    fig, ax = plt.subplots(figsize=(10, 7.6))
    fig.suptitle("Ten trips at 11:00 — hot day: safe routes, coolest fallbacks and replanning",
                 fontsize=12, weight="bold", y=0.97)
    walkways = draw_base(ax, graph, buildings, greens, bounds_of(graph))
    mister_ids, mister_dots, signal_text = actuator_artists(ax, graph, show_labels=False)
    add_colorbar(fig, ax)
    routes = LineCollection([], linewidths=3.2, alpha=0.85, zorder=3, capstyle="round")
    ax.add_collection(routes)
    trails = LineCollection([], linewidths=1.6, colors="#252525", alpha=0.5, zorder=4)
    ax.add_collection(trails)
    dots = ax.scatter([], [], s=60, edgecolors="black", linewidths=0.8, zorder=7)
    rings = ax.scatter([], [], s=420, facecolors="none", edgecolors="#6a3d9a", linewidths=2.2, zorder=8)
    labels = {p.pedestrian_id: ax.text(0, 0, f"P{p.pedestrian_id}", fontsize=7, weight="bold", zorder=9)
              for p in trips}
    info = info_box(ax)
    ax.legend(handles=[
        plt.Line2D([], [], color=SAFE_COLOR, lw=3, label="safe route"),
        plt.Line2D([], [], color=FALLBACK_COLOR, lw=3, label="coolest fallback (no safe route)"),
        plt.Line2D([], [], marker="o", color="none", markeredgecolor="#6a3d9a", markersize=12,
                   markeredgewidth=2, label="replanned this minute")],
        loc="lower right", fontsize=8, framealpha=0.92)
    history: dict[int, list[tuple[float, float]]] = {}
    flash: dict[int, int] = {}
    last_replan = [""]

    def update(i):
        frame = frames[i]
        style_walkways(walkways, frame, base_width=0.8, unsafe_width=1.6)
        mister_dots.set_facecolors([MIST_ON if frame.misters[m] == "on" else MIST_OFF for m in mister_ids])
        for sid, text in signal_text.items():
            text.set_text(f"{frame.signals[sid]:g}s")
        for change in frame.replanned:
            flash[change["pedestrian_id"]] = 3
            last_replan[0] = (f"P{change['pedestrian_id']} replanned at {frame.time[:5]} -> "
                              f"{change['route_policy'].replace('_', ' ')}")
        segs, colors, xs, ys, dot_colors, ring_xy, trail_segs = [], [], [], [], [], [], []
        walking = 0
        for person in frame.pedestrians:
            pid = person["pedestrian_id"]
            xy = pedestrian_xy(graph, lengths, person)
            history.setdefault(pid, []).append(xy)
            trail = history[pid][-6:]
            trail_segs.extend(zip(trail, trail[1:]))
            color = SAFE_COLOR if person["policy"] == "safe_route" else FALLBACK_COLOR
            label = labels[pid]
            if person["state"] == "arrived":
                label.set_text(f"P{pid} ✓")
                label.set_color("#636363")
            else:
                walking += 1
                label.set_text(f"P{pid}" + (" (signal)" if person["signal_wait_remaining_sec"] > 0 else ""))
                label.set_color("black")
                path = remaining_segments(graph, person, xy)
                segs.extend(path)
                colors.extend([color] * len(path))
            # Stagger labels so pedestrians sharing a path stay readable.
            label.set_position((xy[0] + 7, xy[1] + 7 + 14 * (pid % 2)))
            xs.append(xy[0])
            ys.append(xy[1])
            dot_colors.append("white" if person["state"] == "arrived" else color)
            if flash.get(pid, 0) > 0:
                ring_xy.append(xy)
                flash[pid] -= 1
        routes.set_segments(segs)
        routes.set_color(colors)
        trails.set_segments(trail_segs)
        dots.set_offsets(list(zip(xs, ys)))
        dots.set_facecolors(dot_colors)
        rings.set_offsets(ring_xy if ring_xy else [[math.nan, math.nan]])
        policies = [p["policy"] for p in frame.pedestrians if p["state"] != "arrived"]
        info.set_text(f"Clock         {frame.time}\n"
                      f"Walking       {walking}/{len(frame.pedestrians)}   arrived {len(frame.pedestrians) - walking}\n"
                      f"Safe routes   {policies.count('safe_route')}   fallbacks {policies.count('coolest_fallback')}\n"
                      f"Verification  {frame.verification}\n"
                      f"{last_replan[0]}")
        return []

    save(FuncAnimation(fig, update, frames=len(frames)), out / "2_ten_trips.gif", fps=5)


# -- GIFs 3 and 4: with and without misters --------------------------------------
def zoom_center(graph):
    """The mister zone that covers the most walkway length."""
    from models.environment.model import _segment_circle_coverage_ratio as cover
    best = None
    for a in graph.actuators.values():
        if a.actuator_type != "misting_point":
            continue
        covered = sum(e.distance_m * cover(a.x_m, a.y_m, 10.0, graph.nodes[e.from_node].x_m,
                                           graph.nodes[e.from_node].y_m, graph.nodes[e.to_node].x_m,
                                           graph.nodes[e.to_node].y_m) for e in graph.edges)
        if best is None or covered > best[0]:
            best = (covered, a)
    return best[1]


def make_misting(out, buildings, greens):
    reference, _, _ = load_simulation_graph(USC, 10)
    focus = zoom_center(reference)
    for count, name, title in ((10, "3_with_misters.gif", "Hot day WITH 10 misters"),
                               (0, "4_without_misters.gif", "Same hot day WITHOUT misters")):
        graph, frames = simulate(count, "hot", time_range=("10:00", "17:00"), dt=300)
        fig = plt.figure(figsize=(12, 7.2))
        fig.suptitle(f"{title} — 10:00 to 17:00", fontsize=13, weight="bold", y=0.97)
        ax = fig.add_axes([0.02, 0.03, 0.62, 0.87])
        zoom = fig.add_axes([0.66, 0.27, 0.32, 0.63])
        walkways = draw_base(ax, graph, buildings, greens, bounds_of(graph))
        half = 45
        zoom_walkways = draw_base(zoom, graph, buildings, greens,
                                  (focus.x_m - half, focus.y_m - half, focus.x_m + half, focus.y_m + half))
        zoom.set_title(f"Close-up: {focus.actuator_id.replace('SIM_', '')} zone (10 m circle)", fontsize=9)
        circle = Circle((focus.x_m, focus.y_m), 10.0, fill=False, linestyle="--", linewidth=1.5,
                        edgecolor=MIST_ON if count else "#969696", zorder=6)
        zoom.add_patch(circle)
        ax.add_patch(plt.Rectangle((focus.x_m - half, focus.y_m - half), 2 * half, 2 * half,
                                   fill=False, edgecolor="#08306b", linewidth=1, zorder=6))
        mister_ids, mister_dots, signal_text = actuator_artists(ax, graph, show_labels=False)
        zoom_note = zoom.text(0.03, 0.03, "", transform=zoom.transAxes, fontsize=8, zorder=10,
                              bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#888888", alpha=0.92))
        cax = fig.add_axes([0.66, 0.16, 0.32, 0.025])
        bar = fig.colorbar(plt.cm.ScalarMappable(norm=HEAT_NORM, cmap=HEAT_CMAP), cax=cax,
                           orientation="horizontal")
        bar.set_label("Felt heat, UTCI (°C) — 38 °C and above is unsafe", fontsize=8)
        bar.ax.axvline(38, color="black", linewidth=1.5)
        bar.ax.tick_params(labelsize=7)
        info = info_box(ax)
        total = sum(e.distance_m for e in graph.edges)
        in_zoom = [abs(graph.nodes[e.from_node].x_m - focus.x_m) < half
                   and abs(graph.nodes[e.from_node].y_m - focus.y_m) < half for e in graph.edges]

        def update(i, frame_list=frames, walk=walkways, zwalk=zoom_walkways, dots=mister_dots,
                   ids=mister_ids, box=info, note=zoom_note, g=graph, n=count, tot=total, mask=in_zoom):
            frame = frame_list[i]
            style_walkways(walk, frame)
            style_walkways(zwalk, frame, base_width=3.5, unsafe_width=6.0)
            if ids:
                dots.set_facecolors([MIST_ON if frame.misters[m] == "on" else MIST_OFF for m in ids])
            unsafe_m = length_where(g, frame.unsafe, bool)
            cooled_m = length_where(g, frame.mist_cooling, lambda v: v > 0)
            on = sum(s == "on" for s in frame.misters.values())
            zone = [u for u, keep in zip(frame.utci, mask) if keep]
            box.set_text(f"Time            {frame.time}\n"
                         f"Misters on      {on}/{n}\n"
                         f"Unsafe walkways {unsafe_m / 1000:5.2f} km ({100 * unsafe_m / tot:3.0f}%)\n"
                         f"Misted walkway  {cooled_m:5.0f} m\n"
                         f"Verification    {frame.verification}")
            note.set_text(f"Coolest path here {min(zone):.1f} °C\nHottest path here {max(zone):.1f} °C")
            return []

        save(FuncAnimation(fig, update, frames=len(frames)), out / name, fps=7)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("which", nargs="+", choices=("day", "trips", "misting", "all"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    chosen = {"day", "trips", "misting"} if "all" in args.which else set(args.which)
    buildings, greens = campus_context()
    if "day" in chosen:
        make_day(args.out, buildings, greens)
    if "trips" in chosen:
        make_trips(args.out, buildings, greens)
    if "misting" in chosen:
        make_misting(args.out, buildings, greens)


if __name__ == "__main__":
    main()
