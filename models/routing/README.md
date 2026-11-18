# Routingc pipeline stage

The runner executes **Control → Environment → Heat Exposure → Routing → Pedestrian → Verification**.
Initialization still calculates Environment → Heat Exposure once before control.
Routing and Pedestrian share one persistent simulator and the selected dataset.
The final audit uses STREL for current selected-route safety.

## Run the real campus example

```sh
python main.py --trips models/routing/example_trips.json --rounds 15 --pedestrian-dt 60 --debug-dir debug/real-campus
```

The default map is `dataset/usc_osm_trimmed`: real OSM walkway geometry, with
assumed shade, weather and hypothetical hardware. See its README. Runtime needs
only root requirements; OSMnx is needed only to rebuild the exported map.
Without `--trips`, config.json requests four pedestrians once (two preferences,
both directions between Leavey Library and Seeley G. Mudd Building).

## Inputs and outputs

Trip JSON contains a `pedestrians` array, for example:

```json
{"pedestrians": [{"pedestrian_id": 1, "origin": "Leavey Library",
"destination": "Seeley G. Mudd Building", "preference": 0.8,
"walking_speed_mps": 1.4}]}
```

Origin/destination may be exact building names from the selected map's
`buildings.json`, or exact node IDs. Preference ranges from 0 (shortest distance)
to 1 (strongest comfort weighting). IDs must be unique. An explicit empty array
runs with no demand. Building routing starts at its first entrance and targets
its cheapest reachable destination entrance, matching routingc.

The stage consumes `graph.edges[*].properties.heat_exposure_rate` (degrees C
above comfortable UTCI, supplied by Heat Exposure). It does not use STREL or a
second air-temperature field. Numeric `blocked` values 0/1 are optional; 1
closes an edge to new route assignments. Inaccessible edges are excluded.

Every edge receives numeric `walkability`, `blocked` and `planned_pedestrians`.
The latter counts **newly assigned** routes using that edge this round, not
occupancy. Pedestrian supplies actual `pedestrian_count` after movement.
Diagnostics include new routes, cumulative assigned routes, pending IDs,
distances, cost and estimated travel/exposure. Routing also calls
`PedestrianSimulator.assign_route(id, ordered_node_ids, walking_speed_mps=...)`
before that simulator's predict call. This explicit shared-instance handoff is
retained for route assignment. The runner also forwards same-pass stage
diagnostics to the final verifier. Route records include all resolved destination
entrances; pending records include resolved endpoints and speed.

## Same algorithm, explicit heat adapter

`models/routing/routing_core.py` contains routingc's unchanged Dijkstra,
make_cost, route and set_blocked functions. Both the standalone OSM prototype
and this adapter import them. The pipeline uses a small CampusGraph edge view,
without a second graph class or runtime NetworkX/OSMnx dependency.
The main pipeline does not depend on the optional [demo](demo/README.md) or
[osm_build](osm_build/README.md) folders. They contain standalone visualizations
and tools for rebuilding the exported dataset, respectively.

The prototype used placeholder walkability. The pipeline instead defines:

```
walkability = 1 - min(1, heat_exposure_rate / discomfort_scale_c)
cost = distance_m * (1 + 4 * preference * (1 - walkability))
```

The default discomfort_scale_c is 20; this is a configurable modeling assumption,
not a calibrated thermal preference model. Positive preference penalizes hot
walkways; it does not guarantee a different route. The real-campus example's two
preferences currently choose the same 868 m path with uniform assumed shade.
Estimated exposure is sum(heat_exposure_rate × walking minutes) at assignment
conditions. It is a route estimate, not an accumulated individual exposure log.

## Persistence and compatibility

Requests are consumed once when assigned; agents persist and are never spawned
again each round. Unreachable requests stay pending and are retried next round.
Unknown names fail clearly. A request already at a destination is reported and
consumed without creating an agent. Assignment validation is atomic within this
stage. Preloaded, unmoved simulator agents can be routed before departure;
already moving agents retain their existing route and progress.

`--routes` remains accepted as a legacy **trip request** format: only first/last
node IDs and speed are read; intermediate nodes are recomputed by routingc.
Use the matching legacy map, since Nxx IDs do not exist in the real OSM map:

```sh
python main.py --dataset dataset/campus_50 --routes models/pedestrian/example_routes.json --rounds 5 --pedestrian-dt 60
```

Direct calls to PedestrianSimulator.from_routes_file still assign fixed paths.

## Verification policy and remaining limitations

The final verifier checks safe-first selection, or the coolest fallback when no
safe route exists, plus an independent 2.5x time bound. This router still uses its
original comfort-weighted cost and does not enforce those requirements. Audit
failures are reported without changing assignment or movement. No forecast
inputs are used. See `models/verification/README.md`.

Pedestrians now execute signal delays before controlled departures; see
models/pedestrian/README.md and example_signal_trips.json. Routing cost,
estimated_walk_minutes and estimated walking exposure still exclude those
delays. No midtrip rerouting, crowd avoidance, or personal accumulated heat is
implemented. Blocking an edge affects pending/new routes,
not agents already walking. Time/weather stay fixed while pedestrian elapsed
time advances. Control reacts to heat rather than pedestrian demand and may
oscillate misters between rounds. Complete arrivals remain in snapshots but
have no edge occupancy. Stage diagnostics are saved under the debug directory;
there is no saved-state resume loader or final aggregate route export.
