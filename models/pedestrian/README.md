# Persistent pedestrian simulation

The runner initializes Environment → Heat Exposure once, then executes
Control → Environment → Heat Exposure → Routing → Pedestrian → Verification each round.
The routingc stage assigns ordered node paths to one persistent simulator;
agents retain progress, walking time and signal countdowns between calls.
See models/routing/README.md for trip requests and heat-weighted routing.

## Standalone movement demo

```sh
python -m models.pedestrian.demo
python -m models.pedestrian.demo --steps 20 --dt 5
python -m models.pedestrian.demo --save debug/pedestrian_movement.json
```

The demo defaults to `dataset/usc_osm_trimmed` and the fixed node routes in
`example_usc_routes.json`: Olin Hall of Engineering to Seeley G. Mudd Building
and back, crossing `SIM_S2`. It performs no route search and does not run
Environment, Heat Exposure, or Control. Signal commands come directly from
the loaded dataset (currently a 30-second wait for USC), rather than a hot-weather
Control command. Misters do not affect this movement-only run.

By default it advances ten 10-second steps, keeping the same simulator across
steps. Each route is about 75.48 m; at 1.4 m/s each person takes about 53.92 s
walking plus 30 s waiting, arriving within the 100-second demo. The simulation
clock continues through all requested steps after arrival; individual walking
and waiting totals stop at arrival.

Output shows the initial state and every step: state, distance along the route,
segment and position, walking time, waiting time, and remaining signal delay.
`--save` writes final edge occupancy and all snapshots, including arrival status
and the loaded signal commands. `--steps` must be positive and `--dt` finite and
nonnegative. `--dt 0` inspects occupancy without advancing movement.

Use `--dataset` and `--routes` together to test a different map with matching
fixed routes. The older `example_routes.json` uses synthetic-map node IDs and
is retained for compatibility; it is not the USC demo's default.

## Tests

```sh
python -m unittest discover -s tests/pedestrian -t .
```

Tests live in `tests/pedestrian`; see [the test coverage guide](../../tests/README.md).
Dataset integration tests use USC and derive expected movement from actual
segment distances. They cover bidirectional movement, segment transitions,
signal waiting across steps and command changes, arrival timing, occupancy,
preservation of graph properties, and saved snapshots. Small synthetic unit
tests remain for isolated movement and validation edge cases.

## Run a signal crossing

```sh
python main.py --trips models/routing/example_signal_trips.json --rounds 2 --pedestrian-dt 60 --debug-dir debug/signal-waits
```

These trips walk between Olin Hall of Engineering and Seeley G. Mudd Building
in opposite directions, crossing SIM_S2. Each path is about 75.5 m. With the
initial hot-scenario 10-second command, each pedestrian takes about 53.9 seconds
walking plus 10 seconds waiting. Both arrive in round 2. The final output reports
20 person-seconds of waiting (10 for each person), not 20 seconds per person.
The original Leavey → SGM example avoids both configured signals and therefore
still has zero waiting. Actuator locations remain hypothetical.

## Signal behavior

- Before departing a signal's anchor node on an edge listed in controlled_edges,
  the agent samples that signal's current graph property wait_time_s, as set by
  Control. This applies at the origin and at intermediate crossings, in either
  direction when the outgoing edge is controlled. Merely approaching a signal
  or reaching a final destination does not add another wait.
- The agent remains at position zero on its next route edge and enters WAITING.
  The countdown uses timestep seconds, not a real-world sleep. Delay may span
  rounds. When it ends, the agent resumes walking with any timestep time left.
- The sampled delay is captured once per departure. A later control command
  does not restart or resize an active wait. Revisiting a crossing later in a
  route incurs a new delay; approaching and leaving on the same visit do not
  incur two delays. Co-located signals controlling the same departure are treated
  as concurrent: apply their maximum delay, not their sum.
- This is a deterministic per-arrival delay model, not a shared red/green phase
  clock. All arrivals during a round see that round's command. Other WAITING
  agents without a signal countdown remain paused as before.

## State and outputs

Each agent stores ID, origin/destination node IDs, route, speed, current edge
index, position_m, WALKING/WAITING/ARRIVED state and:

| Field | Meaning |
| --- | --- |
| total_travel_time_sec | Actual walking seconds, preserving the original meaning |
| total_wait_time_sec | Signal waiting seconds actually consumed |
| total_elapsed_time_sec | Walking + waiting in diagnostic snapshots (derived) |
| signal_wait_remaining_sec | Unspent signal delay |
| waiting_signal_ids | Signals responsible for an active countdown |
| signal_wait_edge_index | Route departure already checked, preventing a second charge |

Arrivals retain their state but consume no further travel/wait time. The global
simulation clock advances by the full timestep even after everyone arrives.

predict(ModelInput) reads pedestrian_dt_sec from context (default 1). It receives
the latest graph and writes complete numeric edge pedestrian_count. Waiting
agents count on their next assigned edge at position zero; arrived agents are
excluded. Counts are instantaneous occupancy, not density or cumulative flow.
All earlier graph properties are retained. Diagnostics contain pedestrian_state
and signal_wait_consumed_sec (summed across agents this step). The runner prints
steps that consume delay and final total signal waiting.

A zero timestep leaves agent states unchanged. Registration, step and pipeline
prediction validate data; multi-agent movement commits atomically. Countdown
state and routes are copied across simulator and routing calls. Invalid signal
wait values fail rather than silently allowing passage.

## Direct APIs and legacy routes

PedestrianSimulator.assign_route(id, route, walking_speed_mps=...) accepts
ordered existing node IDs. It registers a new agent or replaces a route before
walking or signal waiting has begun. It validates the complete route before
committing. Movement, exposure-based route optimization and agent state remain
separate responsibilities.

PedestrianSimulator.from_routes_file(graph, path) directly assigns fixed routes
from a JSON pedestrians array containing pedestrian_id, route and optional speed.
In main.py, --routes instead takes the endpoints of that legacy format and lets
routingc recompute the intermediate nodes. Select the matching synthetic map:

```sh
python main.py --dataset dataset/campus_50 --routes models/pedestrian/example_routes.json --rounds 5 --pedestrian-dt 60
```

Use --trips models/routing/example_trips.json for the real OSM map. No --trips
uses the four configured one-time requests. Passing an empty requests array
creates no agents. Programmatic run_simulation callers can provide the existing
simulator, trip_requests and dataset_directory explicitly.

## Current limits

Routing may replace an agent's remaining route each round through
`replace_remaining_route`, which keeps walked nodes, a committed current edge and
its signal countdown, and updates the destination entrance. Crowd collision handling
and accumulated individual heat are not implemented. Environment time/weather
remain fixed while walking/waiting advance, unless `main.py --time-range`
advances the clock. Debug snapshots can be saved, but
there is no saved-state resume loader. See the
[routing guide](../routing/README.md#verification-policy-and-remaining-limitations)
for route policy and travel estimates, and the [control guide](../control/README.md)
for heat-based actuator decisions.
The final verification stage uses STREL and Python to audit current conditions
without changing movement. The runner captures state before movement so
same-step arrivals remain auditable, and the verifier preserves pedestrian
diagnostics in the final output. Route node pairs must uniquely identify edges; the real-map
export splits parallel walkways into unique node chains for this reason.
