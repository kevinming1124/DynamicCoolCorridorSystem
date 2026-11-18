"""Pictures of routes and an animation of the pedestrian simulation.

Needs two extra packages that the rest of the project doesn't (they're only
for drawing, so they're deliberately NOT in requirements.txt):

    python -m pip install matplotlib pillow

Route comparison picture (one trip at three preferences):

    python -m models.routing.visualize
    python -m models.routing.visualize --start "USC Village" --dest "Lyon Center"
    python -m models.routing.visualize --scenario mild
    python -m models.routing.visualize --heat-test     # proof that comfort routing works

Animation: python -m models.routing.simulate --animate

Everything is saved under routing_output/ (ignored by git).
"""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

from .costs import OverrideHeat
from .model import Planner, PlannedRoute

OUTPUT_DIR = Path("routing_output")
ROUTE_STYLES = [  # (preference, color, label)
    (0.0, "#2b6cb0", "fastest (preference 0)"),
    (0.5, "#d69e2e", "balanced (preference 0.5)"),
    (1.0, "#2f855a", "most comfortable (preference 1)"),
]


def _plt():
    """Import matplotlib only when drawing, with a helpful message if it's missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # draw to files, no window needed
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("Drawing needs matplotlib and pillow:\n"
                         "    python -m pip install matplotlib pillow") from exc
    return plt


# ---------------------------------------------------------------------------
# The campus background
# ---------------------------------------------------------------------------
def draw_campus(ax, planner: Planner, show_blocked: bool = True):
    """Every walkway colored by heat, plus places, crosswalk signals and misters.

    Returns the color scale, so the caller can add a legend bar for it.
    """
    plt = _plt()
    nodes = planner.graph.nodes
    rates = [planner.heat.rate(e) for e in planner.edges]
    scale = plt.Normalize(min(rates), max(rates))
    colors = plt.get_cmap("RdYlGn_r")   # reversed: green = cool, red = hot

    for edge in planner.edges.values():
        a, b = nodes[edge.from_node], nodes[edge.to_node]
        blocked = show_blocked and edge.edge_id in planner.blocked
        ax.plot([a.x_m, b.x_m], [a.y_m, b.y_m],
                color="black" if blocked else colors(scale(planner.heat.rate(edge.edge_id))),
                linestyle="--" if blocked else "-",
                linewidth=3 if blocked else 2.2, alpha=0.9 if blocked else 0.55, zorder=1)

    for actuator in planner.graph.actuators.values():
        if actuator.actuator_type == "traffic_signal":
            ax.plot(actuator.x_m, actuator.y_m, "s", color="black", markersize=7, zorder=4)
        elif actuator.actuator_type == "misting_point":
            on = actuator.properties.get("status") == "on"
            ax.plot(actuator.x_m, actuator.y_m, "D", markersize=9, zorder=4,
                    markerfacecolor="#3182ce" if on else "white", markeredgecolor="#3182ce")

    for node in nodes.values():
        if node.location_name:
            ax.plot(node.x_m, node.y_m, "o", color="#4a5568", markersize=5, zorder=3)
            # Wrap long names onto two lines so neighbors' labels don't collide.
            label = "\n".join(textwrap.wrap(node.location_name, 16))
            ax.annotate(label, (node.x_m, node.y_m), xytext=(4, 4),
                        textcoords="offset points", fontsize=7, color="#2d3748", zorder=5)

    ax.set_aspect("equal")
    ax.axis("off")
    return plt.cm.ScalarMappable(norm=scale, cmap=colors)


def _legend_text(ax) -> None:
    ax.text(0.0, -0.02, "black square = crosswalk signal   blue diamond = mister (filled = on)"
            "   black dashes = closed walkway", transform=ax.transAxes, fontsize=8,
            va="top", color="#4a5568")


def _save(fig, path: Path) -> None:
    """Save, warning instead of crashing if the file is locked (e.g. open on Windows)."""
    plt = _plt()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(path, dpi=150, bbox_inches="tight")
        print(f"Saved {path}")
    except OSError as exc:
        print(f"WARNING: couldn't save {path} (close it if it's open, then rerun): {exc}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Route comparison picture
# ---------------------------------------------------------------------------
def draw_routes(planner: Planner, routes: list[tuple[PlannedRoute, str, str]],
                title: str, path: Path) -> None:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(12, 7.5))
    heat_scale = draw_campus(ax, planner)
    nodes = planner.graph.nodes
    # Draw thicker lines first, so overlapping routes all stay visible.
    for i, (planned, color, label) in enumerate(routes):
        xs = [nodes[n].x_m for n in planned.route.nodes]
        ys = [nodes[n].y_m for n in planned.route.nodes]
        ax.plot(xs, ys, color=color, linewidth=9 - 2.5 * i, alpha=0.85, zorder=2,
                solid_capstyle="round", label=label)
    first = routes[0][0].route.nodes
    ax.plot(nodes[first[0]].x_m, nodes[first[0]].y_m, "o", color="black", markersize=13, zorder=6)
    ax.plot(nodes[first[-1]].x_m, nodes[first[-1]].y_m, "*", color="black", markersize=20, zorder=6)
    fig.colorbar(heat_scale, ax=ax, shrink=0.6, label="heat (UTCI C above comfortable)")
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.08), ncol=3, fontsize=9, frameon=False)
    ax.set_title(title, pad=30)
    _legend_text(ax)
    _save(fig, path)


def compare_routes(planner: Planner, start: str, destination: str, path: Path,
                   label: str = "") -> list[PlannedRoute]:
    """Route one trip at three preferences, print the numbers, save the picture."""
    routes = []
    print(f"\n{start} -> {destination}{label}")
    print(f"  {'setting':<34}{'minutes':>8}{'heat exposure':>15}   route")
    for preference, color, text in ROUTE_STYLES:
        planned = planner.route(start, destination, preference)
        if planned is None:
            print(f"  {text:<34}  no route")
            continue
        routes.append((planned, color, text))
        print(f"  {text:<34}{planned.stats.minutes:>8.2f}{planned.stats.exposure:>15.1f}   "
              + " > ".join(planned.route.nodes))
    if routes:
        draw_routes(planner, routes, f"{start} -> {destination}{label}", path)
    return [r[0] for r in routes]


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------
def animate(result, path: Path, fps: int = 20) -> None:
    """Turn a simulate.SimulationResult into a GIF that plays like a video."""
    plt = _plt()
    from matplotlib.animation import FuncAnimation, PillowWriter

    planner = result.planner
    nodes = planner.graph.nodes
    closed_later = {edge_id for _, edge_id in result.closures}

    fig, ax = plt.subplots(figsize=(13, 6))
    # Background: today's heat. Walkways closed DURING the run are drawn later,
    # at the moment they close, so leave them out of the background's dashes.
    initially_blocked = planner.blocked - closed_later
    saved, planner.blocked = planner.blocked, initially_blocked
    heat_scale = draw_campus(ax, planner)
    planner.blocked = saved
    fig.colorbar(heat_scale, ax=ax, shrink=0.6, label="heat (UTCI C above comfortable)")

    closure_lines = []
    for frame_i, edge_id in result.closures:
        edge = planner.edges[edge_id]
        a, b = nodes[edge.from_node], nodes[edge.to_node]
        (line,) = ax.plot([a.x_m, b.x_m], [a.y_m, b.y_m], "--", color="black",
                          linewidth=4, zorder=2, visible=False)
        closure_lines.append((frame_i, line))

    dots = ax.scatter([], [], s=60, c=[], cmap="cool", vmin=0, vmax=1,
                      edgecolors="black", linewidths=0.7, zorder=7)
    # The clock gets its own line under the symbol key.
    clock = ax.text(1.0, -0.08, "", transform=ax.transAxes, ha="right", va="top",
                    fontsize=10, family="monospace")
    ax.set_title("People walking: cyan = speed-seeker, magenta = comfort-seeker",
                 fontsize=11, loc="left", pad=12)
    _legend_text(ax)

    def update(i):
        t, people, counts = result.frames[i]
        if people:
            dots.set_offsets([(x, y) for x, y, _ in people])
            dots.set_array([p for _, _, p in people])
        else:
            dots.set_offsets([[float("nan"), float("nan")]])
        for frame_i, line in closure_lines:
            line.set_visible(i >= frame_i)
        minutes, seconds = divmod(int(t), 60)
        walking = counts["walking"] + counts["at_signal"]
        clock.set_text(f"t = {minutes:02d}:{seconds:02d}   walking {walking:>2}   "
                       f"arrived {counts['arrived']:>2}   stuck {counts['stuck']}")
        return dots, clock

    anim = FuncAnimation(fig, update, frames=len(result.frames), interval=1000 / fps)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        anim.save(path, writer=PillowWriter(fps=fps))
        print(f"Saved {path}")
    except OSError as exc:
        print(f"WARNING: couldn't save {path} (close it if it's open, then rerun): {exc}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def main() -> None:
    from .simulate import prepare_planner

    parser = argparse.ArgumentParser(description="Draw one trip routed at three preferences.")
    parser.add_argument("--start", default="Leavey Library")
    parser.add_argument("--dest", default="Seeley G. Mudd Building")
    parser.add_argument("--time", default="13:00")
    parser.add_argument("--scenario", default="hot")
    parser.add_argument("--forecast", choices=["current", "oracle", "noisy"],
                        help="override config.json's forecast_mode")
    parser.add_argument("--heat-test", action="store_true",
                        help="make the fastest route scorching and check comfort avoids it")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    planner = prepare_planner(args.time, args.scenario, args.forecast)
    routes = compare_routes(planner, args.start, args.dest, args.output_dir / "routes.png")

    if args.heat_test and routes:
        # Controlled experiment: every edge of the fastest route becomes hotter
        # than anything on campus. Speed-seekers should keep it (they ignore
        # heat); comfort-seekers should switch, if any detour is worth it.
        fastest = routes[0]
        hottest = max(planner.heat.rate(e) for e in planner.edges)
        scorching = OverrideHeat(planner.heat, {s.edge_id: hottest * 2 for s in fastest.route.steps})
        tested = Planner(planner.graph, planner.settings, planner.context, heat=scorching)
        after = compare_routes(tested, args.start, args.dest,
                               args.output_dir / "routes_heat_test.png",
                               label="  [HEAT TEST: fastest route made scorching]")
        changed = after[-1].route.nodes != after[0].route.nodes
        print("\nHEAT TEST " + ("PASSED: the comfortable route avoids the scorching one."
                                if changed else
                                "INCONCLUSIVE: no detour was worth it for this trip; try another."))


if __name__ == "__main__":
    main()
