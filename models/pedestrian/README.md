# Pedestrian pipeline interface

The simulator executes externally assigned node routes. It does not calculate
shortest paths, choose cooler routes, or create pedestrian demand.

## Assign routes explicitly

```python
from models.pedestrian.model import PedestrianSimulator
from utils import load_campus_graph

graph = load_campus_graph("dataset/campus_50")
simulator = PedestrianSimulator(graph)
simulator.assign_route(1, ["N01", "N02", "N03"], walking_speed_mps=1.4)
```

`assign_route(pedestrian_id, route, walking_speed_mps=None)` creates a new agent
from the route endpoints. The default speed for a new agent is 1.4 m/s. An
existing agent's route may be replaced only before departure, preserving its
origin, destination, state, accumulated times, and speed unless a speed is
explicitly provided. Mid-trip rerouting is deferred; a replacement after any
movement raises an error. Missing paths should be handled by the routing caller;
empty, disconnected, unknown-node, and inaccessible routes are rejected.
Assignments are copied and validated before replacing existing state.

Existing `add_pedestrian`, `step(dt)`, and `get_state()` calls remain supported.
Use either `step` or `predict` for an interval, not both.

## Load external routing results

The optional route file has this format:

```json
{
  "pedestrians": [
    {
      "pedestrian_id": 1,
      "route": ["N01", "N02", "N03"],
      "walking_speed_mps": 1.4
    }
  ]
}
```

`walking_speed_mps` is optional. Duplicate IDs are rejected in a file. Load it
with `PedestrianSimulator.from_routes_file(graph, path)`, or let the runner do so:

```sh
python main.py --routes models/pedestrian/example_routes.json --rounds 5 --pedestrian-dt 1
```

The example file contains two illustrative preassigned routes, valid on both
bundled datasets. It does not claim to be an optimizer's output. Without
`--routes`, the command runs an empty pedestrian population rather than
inventing agents. Future routing code can call `assign_route` directly.

## Pipeline lifecycle

Initialization runs Environment -> Heat Exposure. Each round then runs:

```text
Control -> Environment -> Heat Exposure -> Pedestrian
```

The runner constructs its stage list once and reuses one simulator across all
rounds. Programmatic callers can pass their initialized simulator using
`run_simulation(graph, rounds=5, pedestrians=simulator, context=...)`.
The final result is now the pedestrian output; heat outputs remain on its graph.

`predict(ModelInput) -> ModelOutput` receives the newest graph, preserves its
properties and actuator state, retains agents and elapsed time, and advances
by `context["pedestrian_dt_sec"]` (default 1.0). The timestep must be finite and
nonnegative; zero reports occupancy without moving. Updates commit only when
movement and output validation both succeed.

The environment's `time` and `scenario` stay fixed across rounds, matching
master's existing behavior. Pedestrian elapsed seconds advance independently;
this is a fixed-weather integration experiment, not a synchronized day-long
simulation. A shared advancing clock is deferred.

## Outputs and handoff

Every edge receives `pedestrian_count`, its instantaneous end-of-step occupancy.
Empty edges get zero; arrivals are excluded. Waiting agents count on their
assigned edge, even at position zero. Counts are not density or forecasts.
Routes are bidirectional node sequences; ambiguous parallel active edges raise
an error instead of silently assigning counts to the wrong walkway.

The final output's `diagnostics["pedestrian_state"]` provides individual agent
snapshots, simulation time, and population summaries. The runner prints a brief
summary. Debug output includes the pedestrian stage in every round.

Upstream heat properties and counts pass to later stages with the graph. The
runner preserves supplied context, but does not automatically copy diagnostics
into it. A caller needing individual snapshots can explicitly use:

```python
next_input = output.to_model_input(context={
    **context,
    "pedestrian_state": output.diagnostics["pedestrian_state"],
})
```

Controller currently uses `unsafe_heat` and, for LEDs, `walking_heat_exposure`;
it does not consume pedestrian occupancy. Temperature is not required by
movement. Signal waiting/release, exposure accumulation, mid-trip rerouting,
forecasting, and the STREL route-metadata contract are not implemented here.
