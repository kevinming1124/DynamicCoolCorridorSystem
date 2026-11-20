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
| Environment | `environment/test_shade.py` | USC shade bounds and time variation; consistent shade within original walkways; explicit dataset schedules; no schedule leakage between datasets or implicit fallback |
| Environment misting | `environment/test_misting.py` | Analytic circle/segment intersections, including partial and full overlap, tangency, zero length, and zero radius; coverage-scaled cooling and humidity; off state; independent actuator audit detects incorrect cooling |
| Heat Exposure | `heat_exposure/test_model.py` | USC standalone/pipeline agreement on every edge; finite outputs and valid heat flags; shade reduces radiant temperature and heat; walking exposure scales with duration; saved run context; invalid mister IDs; alternate dataset selection |
| Control | `control/test_model.py` | Radius-based observation of segments disconnected from the anchor; exclusion of boundary-only contact, tangency, and zero-length segments; invalid heat flags and empty zones; legacy LEDs left unchanged; wrong commands detected; hot/mild USC checks for 0/4/6/10 layouts; one decision without effect recalculation; saved checks; four-mister pipeline command |
| Pedestrian coordinator | `pedestrian/test_model.py` | Registration and route validation; duplicate IDs; multiple agents; simulation clock; arrivals; defensive copies and independent snapshots; atomic updates and rollback; route replacement restrictions; signal countdown and occupancy preserved across graph updates |
| Pedestrian movement | `pedestrian/test_movement.py` | Distance and segment transitions; reverse travel; invalid routes, positions, speeds, timesteps, and inaccessible edges; waiting and arrived states; signal timing, partial waits, command changes, exact release, repeated visits, overlapping signals, and timestep consistency |
| Pedestrian USC integration | `pedestrian/test_dataset_integration.py` | Fixed signal-crossing routes on USC; distance-derived expectations; bidirectional arrival; timestep carryover; saved countdowns after command changes; full-route walking/waiting totals; small/large timestep agreement; occupancy including waiting agents; input graph preservation; demo snapshots and options |
| Verification | `verification/test_model.py` | Three-round USC pipeline with four misters and arrivals; audit preserves graph inputs, commands, and evidence; deliberate incorrect cooling and waiting detected; unsafe selected route rejected when a safe alternative exists; prior failures persist after arrival; missing evidence rejected |
| Shared graph layouts | `graph/test_misting_scenarios.py` | Nested 0/4/6/10 mister layouts preserve geometry and signals; invalid counts, configuration, anchors, and overlapping zones rejected; pipeline option defaults and saved layout metadata |

## Datasets and fixtures

- USC integration tests use `dataset/usc_osm_trimmed` and its actual geometry,
  source-walkway grouping, actuators, and optional misting layouts.
- Pedestrian integration uses the fixed USC routes in
  `models/pedestrian/example_usc_routes.json`. Verification uses the signal trips
  in `models/routing/example_signal_trips.json` through the real pipeline.
- Small synthetic graphs isolate exact geometry, signal, and invalid-input cases.
- Selected Environment and Heat Exposure checks also load `campus_50` to verify
  compatibility and explicit schedule selection; USC remains the standard
  integration dataset.
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
