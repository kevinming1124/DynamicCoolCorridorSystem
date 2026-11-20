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
above comfortable UTCI, supplied by Heat Exposure). Numeric `blocked` values 0/1 are optional; 1
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

`models/routing/routing_core.py` contains the shared Dijkstra,
make_cost, route and set_blocked functions. Both the standalone OSM prototype
and this adapter import them. The pipeline uses a small CampusGraph edge view,
without a second graph class or runtime NetworkX/OSMnx dependency.
The main pipeline does not depend on the optional [demo](demo/README.md) or
[osm_build](osm_build/README.md) folders. They contain standalone visualizations
and tools for rebuilding the exported dataset, respectively.

## Heat-safety policy

Each trip is planned with the current round's heat (`models/routing/policy.py`):

1. **A safe route exists** (only walkways with `unsafe_heat` = 0): choose among
   safe walkways only, using routingc's distance/comfort cost so the preference
   still matters (0 = shortest, 1 = most comfortable). If that route takes more
   than `maximum_arrival_ratio` (2.5) times the fastest route, use the fastest
   safe route; safety wins if even that is too slow.
2. **No safe route**: choose the coolest fallback, which has the least cumulative
   exposure (rate x walking-plus-waiting minutes), then the lower hottest
   segment, then shorter time. It is labelled with how far its hottest segment
   goes past the unsafe line (`required_threshold_relaxation_c`).

Travel time and exposure include signal waits at controlled departures. Route
records include `route_policy`, `estimated_travel_minutes_with_waits`,
`estimated_exposure_with_waits_degree_min`, `maximum_utci_c`,
`within_time_bound` and `policy_note`. This is an independent implementation
of the rules the final Verification stage audits.

The comfort cost used among safe walkways is:

```
walkability = 1 - min(1, heat_exposure_rate / discomfort_scale_c)
cost = distance_m * (1 + 4 * preference * (1 - walkability))
```

The default discomfort_scale_c is 20; this is a configurable modeling assumption,
not a calibrated thermal preference model. On the USC map shortest and most
comfortable safe routes often coincide because the map has few real
alternatives. Shade varies by original walkway using Environment's
generated profiles; these are simulation assumptions, not measured shadows.
Estimated exposure is sum(heat_exposure_rate × walking minutes) at assignment
conditions. It is a route estimate, not an accumulated individual exposure log.

## Persistence and compatibility

Requests are consumed once when assigned; agents persist across rounds.
Unreachable requests stay pending and are retried next round.
Unknown names fail clearly. A request already at a destination is reported and
consumed without creating an agent. Assignment validation is atomic within this
stage. Preloaded, unmoved simulator agents can be routed before departure.

## Replanning

Every round, each walking pedestrian assigned by this stage has its remaining
route recomputed with the same policy. Replanning starts at the end of a
committed current edge (partly walked, or with a sampled signal wait), otherwise
at the current node; the committed edge and its countdown are kept. The route is
replaced only when the result differs, using
`PedestrianSimulator.replace_remaining_route`. If the committed edge is itself
unsafe, no fully safe journey remains, so the coolest fallback is used.
Diagnostics list `replanned_routes` for the round and `replan_count_total`.

`--routes` remains accepted as a legacy **trip request** format: only first/last
node IDs and speed are read; intermediate nodes are recomputed by routingc.
Use the matching legacy map, since Nxx IDs do not exist in the real OSM map:

```sh
python main.py --dataset dataset/campus_50 --routes models/pedestrian/example_routes.json --rounds 5 --pedestrian-dt 60
```

Direct calls to PedestrianSimulator.from_routes_file still assign fixed paths.

## Verification policy and remaining limitations

The final verifier independently checks safe-first selection, or the coolest
fallback when no safe route exists, plus the 2.5x time bound. The router follows
the same policy, so it should pass; a safe route that cannot meet the time bound
is still chosen and fails only the arrival check. See the
[verification guide](../verification/README.md) for audit behavior.

Pedestrians now execute signal delays before controlled departures; see
models/pedestrian/README.md and example_signal_trips.json. The policy's travel
time and exposure include those delays; the older estimated_walk_minutes and
estimated walking exposure fields still exclude them. Blocking an edge affects
new routes and replanned remaining routes. See the
[pedestrian guide](../pedestrian/README.md#current-limits) for movement and
persistence limitations. Stage diagnostics include route records and can be
saved under the debug directory.
