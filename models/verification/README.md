# Current-condition requirements audit

Verification runs last in every round:

```text
Control -> Environment -> Heat Exposure -> Routing -> Pedestrian -> Verification
```

This stage reports audit results while preserving routes, commands, and movement.
All route assessments use the current round's heat and signal settings.

## Route policy

For each pedestrian's remaining journey:

1. If any accessible, unblocked safe route exists, the selected route must be safe.
   Safety uses Heat Exposure's authoritative `unsafe_heat` flag (currently UTCI
   >= 38 C is unsafe). That flag is calculated before UTCI is rounded for output.
2. If none exists, the selected available route must minimize, in order:
   maximum `heat_exposure_rate`, time-weighted cumulative exposure, travel time.
   There is no heat ceiling excluding the coolest fallback. A correct fallback
   can pass route selection while its `selected_route_safe` result is false.
3. Independently, remaining journey time must not exceed 2.5 times the fastest
   available remaining journey. The reference ignores heat safety. This bound
   never filters safe routes or fallback candidates. It can fail even when
   route selection passes.

Travel time includes walking at the pedestrian's speed and departure signal
waits. Cumulative exposure is sum(rate * (walking seconds + waiting seconds) / 60),
in degree-minutes. The outgoing edge's rate approximates the waiting location.
These are snapshot estimates, not personal accumulated heat or future predictions.

Routes to any valid destination entrance resolved by Routing are considered.
At an unstarted departure, alternatives begin at that node. Once walking or a
sampled signal departure has begun, every comparison shares the unavoidable
remaining current edge and its saved countdown. No mid-edge reversal is assumed.
Past segments are excluded. Externally paused agents have an inconclusive arrival
assessment because their release time is unknown.

## Evidence and pipeline handoff

`main.run_pipeline()` creates fresh `context["stage_diagnostics"]` each pass,
keyed by actual stage model names, and captures a copy of simulator state in
`context["pedestrian_before_movement"]` immediately before Pedestrian runs.
Verification locates the current-round reports by their identifying fields.
Missing or malformed required evidence raises an explicit input error.

| Producer | Required evidence |
| --- | --- |
| Heat Exposure | Complete edge `utci_c`, `unsafe_heat`, `heat_exposure_rate`; diagnostic `unsafe_utci_c`, `reference_utci_c` |
| Environment | Complete edge `air_temp_f`, `humidity_pct`, `mist_cooling_f`, `uncooled_air_temp_f`, `uncooled_humidity_pct`; diagnostics `base_air_temp_f`, `base_humidity_pct`, `mist_range_m`, `mist_humidity_increase_pct` |
| Routing | `all_assigned_routes` including destination entrances, `pending_requests` with resolved endpoints and speed, `pending_trip_ids`, `already_at_destination_ids`; optional numeric edge `blocked` |
| Pedestrian | Before/after snapshots with routes, progress, speeds, states, walking/wait totals, countdowns and sampled departures; requested timestep |

Each pedestrian's snapshot carries its route and progress, allowing multiple
pedestrians to share edges in different directions.

Verification audits both the pre-movement routes and post-movement remaining
routes. Thus same-step arrivals cannot hide a bad route selection. Arrived
agents are not applicable in later route audits. Pending unreachable requests
and already-at-destination requests are reported explicitly. No demand makes
route checks not applicable while environmental/command checks can still pass.

## Engines and actuator scope

MoonLight/STREL checks only the selected ordered linear route. The monitor uses
current safe/unsafe flags, preventing another safe campus path from hiding an
unsafe selected segment. It caches the compiled monitor and uses MoonLight
0.3.1's numeric Java entry point to avoid full signal/graph console dumps.

Python uses safe-subgraph Dijkstra for safe-route existence, time Dijkstra for
the independent arrival reference, then minimax Dijkstra followed by additive
exposure/time Dijkstra for the fallback.

Actuator verification checks the effects actually implemented:

- Misting: on/off state, each segment's covered share of each circle within the
  configured radius, cooling equal to full strength times that share (1 inside
  the pre-cut pieces, 0 outside) with the strongest mister counting and no
  stacking or distance decay, rounded cooling and air temperature, the
  implemented humidity increase/clamp, and uncooled air temperature and
  humidity equal to the base weather.
- Signals: operating bounds and independent before/after movement accounting.
  Waits apply at controlled departures; co-located signals use their maximum.
  Existing countdowns are retained across command changes. Walking, waiting,
  position, arrival, and once-per-departure fields must agree with the timestep.

Heat prediction is not implemented; estimates use current conditions. Thermal
exposure reduction attribution is not applicable because the audit does not
calculate a no-misting thermal comparison. Actuator checks cover misting and
signal behavior described above.

## Results

Full details are in `output.diagnostics["verification"]`:

- `before_movement` and `remaining_routes`: per-pedestrian safety, route selection,
  fallback, arrival estimates, optimum fallback when relevant, and violations.
- `pending_requests`, `already_at_destination_ids`, `actuator_response`.
- Current-round `status`, `route_selection_status`, `bounded_arrival_status`,
  `fallback_status`; compact `round_history` and cumulative `simulation_status`.

An unsafe but optimal fallback is labeled `COOLEST_FALLBACK`; it is not labeled
thermally safe. Route-policy failures are `POLICY_VIOLATION`.

Five complete numeric edge properties repeat the corresponding global round
results: `verification_route_selection_valid`,
`verification_bounded_arrival_valid`, `verification_fallback_valid`,
`verification_actuator_response_valid`, `verification_overall_valid`.

| Code | Meaning |
| --- | --- |
| 1 | PASS |
| 0 | FAIL |
| -1 | NOT_APPLICABLE |
| -2 | INCONCLUSIVE |

A failure takes precedence over an inconclusive result, then a pass. Not
applicable checks are excluded, and a group with no applicable checks remains
not applicable. The overall round result covers applicable requirements; inspect
individual check statuses for scope. Prior failed rounds remain visible in
`simulation_status` after arrival. Final diagnostics preserve `pedestrian_state`
and `signal_wait_consumed_sec` for existing consumers.

Debug output is `round_001/06_requirements_verification.json` after the existing
routing and movement files. The router's comfort-weighted cost can select routes
that fail the audited policy.

## Run and validate

```powershell
python main.py --debug-dir debug/verification
python main.py --trips models/routing/example_signal_trips.json --rounds 3 --pedestrian-dt 60 --debug-dir debug/verification-signals
python -m unittest tests.verification.test_model
python -m unittest discover -s tests -t . -v
python -m compileall -q main.py utils models
```

Verification runs through the main pipeline; it has no standalone demo.
For a USC audit with four misters and signal-crossing trips, use:

```powershell
python main.py --misting-count 4 --trips models/routing/example_signal_trips.json --time 13:00 --scenario hot --rounds 3 --pedestrian-dt 30 --debug-dir debug/verification-usc
```

The debug directory contains separate stage outputs for each round. The final
verification file contains the current detailed audit, compact round history,
and pedestrian state. A detected policy violation is an audit result; the
verifier reports it without changing routes or commands.

Tests live in `tests/verification`; see [the test coverage guide](../../tests/README.md).
They obtain evidence directly from the USC pipeline and deliberately corrupt
selected inputs to confirm detection of incorrect cooling, inconsistent waiting,
unsafe route selection when a safe alternative exists, and missing evidence.
They also check that graphs, commands, and evidence remain unchanged and that
prior failures survive arrival.
Java 21+ with `JAVA_HOME` and root Python requirements remain necessary.
