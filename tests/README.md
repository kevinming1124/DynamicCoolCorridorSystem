# Automated test coverage

This folder contains the project's automated checks, grouped by model and
shared graph utilities. They check expected behavior after changes; they do not
run automatically during `main.py` or standalone demos. Runtime model code and
demos remain under `models/`.

## Run the tests

From the repository root, use the project Python environment with
`requirements.txt` installed. The full suite includes the actual heat model and
MoonLight/STREL verifier, so it requires the same dependencies as the pipeline:
Python 3.12 and Java 21+ with `JAVA_HOME` configured.

```powershell
python -m unittest discover -s tests -t . -v
```

Run one group or one file:

```powershell
python -m unittest discover -s tests/pedestrian -t . -v
python -m unittest tests.verification.test_model
python -m unittest tests.environment.test_shade
```

Keep `-t .` in discovery commands so tests are imported through the `tests`
package and identically named files in different groups stay distinct. Individual
test failures identify the model, test method, and failed expectation. Some tests
intentionally introduce invalid inputs or incorrect outputs: passing means the
expected error or FAIL verdict was correctly detected.

## Coverage by area

| Area | Test files | Behaviors checked |
| --- | --- | --- |
| Environment | `environment/test_shade.py` | USC shade bounds and time variation; consistent shade within original walkways; USC uses generated shade without a schedule; cut mist-zone pieces keep their walkway's shade |
| Environment misting | `environment/test_misting.py` | Analytic circle/segment intersections, including partial and full overlap, tangency, zero length, and zero radius; covered-share cooling and humidity on an uncut edge; on a cut walkway only the inside piece is cooled; strongest mister counts without stacking; boundary slivers count as outside; uncooled temperature and humidity equal base weather; off state; independent actuator audit detects incorrect cooling and uncooled values |
| Environment local offsets | `environment/test_local_offsets.py` | Surface and nearby-feature rules, boundaries, open-plaza rule and clamping; invalid rules rejected; surroundings file covers every USC segment and matches OSM (>=95%, median <=1 m); offsets vary and feed both air temperatures; cut pieces share their walkway's offset; audit detects inconsistent offsets; graphs without a surroundings file get none |
| Heat Exposure | `heat_exposure/test_model.py` | USC standalone/pipeline agreement on every edge; finite outputs and valid heat flags; shade reduces radiant temperature and heat; uncooled UTCI equals UTCI without misting and ignores mist cooling; walking exposure scales with duration; saved run context; invalid mister IDs; |
| Control | `control/test_model.py` | Radius-based observation of segments disconnected from the anchor; 35/33 C dead band keeps the current state between thresholds; decisions use uncooled UTCI, not cooled UTCI; threshold configuration validated; exclusion of boundary-only contact, tangency, and zero-length segments; invalid heat flags and empty zones; legacy LEDs left unchanged; wrong commands detected; hot/mild USC checks for 0/4/6/10 layouts; one decision without effect recalculation; saved checks; four-mister pipeline command |
| Routing policy | `routing/test_policy.py` | Safe route beats a shorter unsafe route for every preference; coolest fallback minimizes total exposure, not distance; signal waits count toward exposure and time; time bound falls back to the fastest safe route and safety wins; forced fallback and unreachable trips; remaining-route replacement keeps progress and the committed edge; routing stage replans when heat changes and leaves unchanged routes alone |
| Pedestrian coordinator | `pedestrian/test_model.py` | Registration and route validation; duplicate IDs; multiple agents; simulation clock; arrivals; defensive copies and independent snapshots; atomic updates and rollback; route replacement restrictions; signal countdown and occupancy preserved across graph updates |
| Pedestrian movement | `pedestrian/test_movement.py` | Distance and segment transitions; reverse travel; invalid routes, positions, speeds, timesteps, and inaccessible edges; waiting and arrived states; signal timing, partial waits, command changes, exact release, repeated visits, overlapping signals, and timestep consistency |
| Pedestrian USC integration | `pedestrian/test_dataset_integration.py` | Fixed signal-crossing routes on USC; distance-derived expectations; bidirectional arrival; timestep carryover; saved countdowns after command changes; full-route walking/waiting totals; small/large timestep agreement; occupancy including waiting agents; input graph preservation; demo snapshots and options |
| Verification | `verification/test_model.py` | Three-round USC pipeline with four misters and arrivals; audit preserves graph inputs, commands, and evidence; deliberate incorrect cooling and waiting detected; unsafe selected route rejected when a safe alternative exists; prior failures persist after arrival; missing evidence rejected; fallback ranking puts total exposure before the hottest segment, with ties falling through to later keys; noon USC trips accepted as coolest fallback |
| Pipeline clock | `pipeline/test_time_range.py` | Per-round environment times with 30-minute refreshes; round counts for partial final steps; unchanged fixed-time defaults; `--time-range` defaults (08:00-17:00, 60 s, 540 rounds); invalid ranges, daylight limits, timesteps, intervals and excess rounds rejected; each round's Environment call receives its scheduled time on USC |
| Pipeline round hook | `pipeline/test_on_round.py` | `run_simulation(on_round=...)` is called once per round with the environment time, the round's final output and a separate copy of every stage's diagnostics |
| Shared graph layouts | `graph/test_misting_scenarios.py` | Nested 0/4/6/10 mister layouts preserve geometry and signals; invalid counts, configuration, anchors, and overlapping zones rejected; pipeline option defaults and saved layout metadata |
| Mist-zone cuts | `graph/test_mist_zones.py` | Walkways cut where they cross a circle (twice, once, or not at all for inside, tangent and outside edges); pieces chain in walking order with proportional distance and walking time; boundary nodes on the circle; every piece fully inside or outside; signal references follow pieces; invalid radius and double cutting rejected; no misters leaves the graph unchanged; USC cuts grow with 0/2/4/10 misters |

## Datasets and fixtures

- USC integration tests use `dataset/usc_osm_trimmed` and its actual geometry,
  source-walkway grouping, actuators, and optional misting layouts.
- Pedestrian integration uses the fixed USC routes in
  `models/pedestrian/example_usc_routes.json`. Verification uses the signal trips
  in `models/routing/example_signal_trips.json` through the real pipeline.
- Small synthetic graphs isolate exact geometry, signal, and invalid-input cases.
- Only USC is used. `campus_50` and `tiny_campus_block` remain in the
  repository but no test loads them.
- Tests that save output use temporary directories. Tests do not rewrite the
  dataset files.

## Limits and maintenance

This guide describes behavioral coverage, not a measured line-coverage percentage.
The suite is not exhaustive. Routing is exercised through pipeline integration
and verification tests but has no dedicated test group yet. It does not establish
routing correctness for every map, preference, closure, or policy combination.
Template code and optional OSM rebuilding/visualization tools have no dedicated
tests here.

The checks establish consistency with implemented assumptions; they do not
validate simulated shade, weather, or cooling against field measurements. They
also do not establish physical heat-exposure reduction, crowd behavior, future
weather accuracy, or large-scale performance.

Place new checks under the relevant group, name files `test_*.py`, and keep each
group's `__init__.py` for discovery. Use a small fixture for precise boundary
cases and USC for dataset/pipeline integration. Update this guide when adding or
removing meaningful coverage. Changes to file locations may require adjusting
dataset paths resolved from `__file__`.
