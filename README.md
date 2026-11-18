# Dynamic Cool Corridor System

New developers and GPT sessions should begin with [HANDOFF.md](HANDOFF.md).

## Python version

- 3.12

## Java requirement

The MoonLight STREL engine requires Java 21 or newer and a `JAVA_HOME`
environment variable that points to the installed JDK.

## Environment setup

Create and activate a virtual environment, then install the project
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The root requirements install pythermalcomfort for UTCI calculations and MoonLight 0.3.1 for STREL
OSMnx and plotting packages are optional, for rebuilding/running the standalone prototype.

## Project structure

- DynamicCoolCorridorSystem/
    - utils/
        - graph/ (shared nodes, edges, actuators, graph state, and JSON loading)
        - pipeline/ (shared model input and output contracts)
    - models/
        - _template/ (copyable, human-editable model starter)
        - verification/ (final hybrid STREL and Python requirements verifier)
    - dataset/
        - tiny_campus_block/ (9-node fictional test graph)
    - main.py

## Creating a model

Start with [`models/_template`](models/_template/README.md). Each model keeps
human-tunable values in a small `config.json`, declares zero or more input
and output properties, and exposes the shared Python input/output interface.
Models that only update existing graph state may report no output properties.

Run the template example and checks from the project root:

```powershell
python -m models._template.model
```

## Running the pipeline

Run every configured model stage from the project root:

```powershell
python main.py
```

Select one of the proposed USC misting layouts without copying the map:

```powershell
python main.py --misting-count 4 --debug-dir debug/misting-4
python main.py --misting-count 6 --debug-dir debug/misting-6
python main.py --misting-count 10 --debug-dir debug/misting-10
python main.py --misting-count 0 --debug-dir debug/misting-0
```

The layouts use the first 4, 6, or 10 ordered placements in
`dataset/usc_osm_trimmed/misting_scenarios.json`; zero is the no-misting baseline.
Only misting actuators are replaced in memory. Existing signals, map geometry,
buildings, and shade are shared. Every mister starts off and uses the same 5.4 F
full cooling strength. The environment configuration supplies the shared radius
(currently 10 m); startup rejects overlapping or touching cooling circles and
invalid anchors. Placement names refer to hypothetical locations on the graph.

Omitting `--misting-count` preserves the dataset's original actuators (two
misters on USC). Explicit scenarios require a matching `misting_scenarios.json`
in the selected dataset; these layouts are provided only for USC. Debug outputs
from every stage record `diagnostics.misting_scenario`, including count, radius,
positions, cooling strengths, and preserved non-misting actuator IDs. Keep trips,
weather, rounds, and pedestrian timestep equal when comparing layouts. The zero
baseline retains signal control, so it isolates the effect of adding misting.

Save the complete output of each stage for debugging:

```powershell
python main.py --debug-dir debug
```

The dataset is loaded once. Initialization runs Environment -> Heat Exposure
to supply the controller's inputs. Each round then runs Control -> Environment
-> Heat Exposure -> Routing -> Pedestrian -> Verification, carrying the updated graph into the next round.
One pedestrian simulator is reused for every round. By default,
one round runs after initialization. Choose a positive number of rounds:

```powershell
python main.py --rounds 5 --time 13:00 --scenario hot --debug-dir debug
```

Time and scenario stay fixed across rounds. Each round's environment applies
the latest misting commands, then heat exposure is recalculated. Final heat
results reflect the final round's misting state. Signals and LEDs reflect the
control decision made at the start of that round.

Debug files are grouped under `initialization/` and `round_001/`, `round_002/`,
and so on. The completion output lists final actuator states.

`build_initialization_pipeline()` defines the initialization stages;
`build_pipeline()` defines one round. `run_pipeline()` executes a single pass,
and `run_simulation()` initializes and repeats the rounds. The final output is
the verification audit, preserving pedestrian diagnostics and environmental/heat properties. Each stage receives
the graph produced by the previous stage, including accumulated properties.

## Requirements verification audit

Verification audits every round without changing routes, commands, or movement.
It requires a safe route whenever one exists; otherwise it checks the coolest
fallback by maximum exposure, cumulative exposure, then travel time. The 2.5x
arrival bound is checked independently and never restricts route selection.
All checks use current conditions; there is no heat prediction or forecast input.

Run a tiny-map example with the real upstream models:

```powershell
python -m unittest models.verification.test_model
```

Per-pedestrian results, actuator checks and round history are in
`diagnostics.verification`, saved as `round_001/06_requirements_verification.json`
when debugging is enabled. Existing routing can fail these audit requirements;
verification does not enforce them. See
[`models/verification/README.md`](models/verification/README.md) for scope,
input evidence, result codes and commands.

## Testing dataset

[`dataset/tiny_campus_block`](dataset/tiny_campus_block/README.md) contains a
small campus graph with node coordinates, location categories, walkable edges,
distances, accessibility information, static controllable infrastructure, and
loader-derived initial actuator state.

## Routing and pedestrians on the real USC map

The default dataset is now `dataset/usc_osm_trimmed`, exported from routingc's
OSM network. See [map assumptions](dataset/usc_osm_trimmed/README.md): real
walkway geometry, assumed shade/weather, hypothetical misters and signals.

```sh
python main.py --trips models/routing/example_trips.json --rounds 15 --pedestrian-dt 60 --debug-dir debug/real-campus
```

Routingc computes paths before Pedestrian moves. The example requests two
Leavey → SGM trips with preferences 0 and 1; they are assigned once and retain
progress between rounds. Without --trips, config requests four trips once.
Routing diagnostics are saved as `round_001/04_routingc_model.json`; movement
snapshots are in `05_pedestrian_simulation.json` each round.

See [routing contract and limitations](models/routing/README.md) and
[pedestrian state/movement](models/pedestrian/README.md). Routing reads the
current Heat Exposure output, converts it to comfort weights, and uses
routingc's shared search/cost functions. The final audit uses STREL for
selected-route safety; routing itself remains unchanged.

For old node-route examples, select their matching graph explicitly:

```sh
python main.py --dataset dataset/campus_50 --routes models/pedestrian/example_routes.json --rounds 5 --pedestrian-dt 60
```

In the runner, legacy --routes now supplies origin/destination endpoints;
routingc recomputes the intermediate nodes. The simulator's direct fixed-route
API is unchanged. --dataset dataset/tiny_campus_block remains available with
trip requests using that dataset's node IDs.

Environment time/weather remain fixed; --pedestrian-dt only advances walking.
Pedestrians execute controller-provided signal waits at controlled departures.
Midtrip rerouting, personal accumulated heat and crowd avoidance remain unimplemented. Control still uses heat rather than pedestrian demand.

## Signal-wait example

```sh
python main.py --trips models/routing/example_signal_trips.json --rounds 2 --pedestrian-dt 60 --debug-dir debug/signal-waits
```

These Olin ↔ SGM trips cross SIM_S2 and wait before proceeding. The original
Leavey ↔ SGM example bypasses the configured signals. Wait countdowns persist
between rounds; total_travel_time_sec remains walking time and
total_wait_time_sec records actual waiting. See models/pedestrian/README.md
for departure matching, concurrent signals and the fixed-delay assumption.
