# Dynamic Cool Corridor System handoff

This document is the starting point for a new developer or a new GPT session.
It describes the current implementation and how to extend the project.

## Current state

- Python version: 3.12
- Java version: 21 or newer, with `JAVA_HOME` configured
- Third-party dependencies: `pythermalcomfort`, `MoonLight 0.3.1` and its transitive dependencies
- Dependency management: `requirements.txt`
- Initialization: Environment -> Heat Exposure, once per simulation
- Round stages: Control -> Environment -> Heat Exposure -> Routing -> Pedestrian -> Verification (`--rounds`, default 1)
- One persistent simulator and routingc stage; trip requests via `--trips` or legacy `--routes` endpoints
- Pedestrian timestep: `--pedestrian-dt`, default one second per round (60 s with `--time-range`)
- Clock: `--time` holds environment time fixed (default 12:00). `--time-range [HH:MM-HH:MM]` (default 08:00-17:00) advances the clock by the timestep each round and refreshes Environment every `--env-update-min` minutes (default 30); `main.build_time_schedule` gives each round's time. The scenario stays fixed in both modes.
- Each round receives the previous round's graph
- Dataset: only `usc_osm_trimmed` (real OSM geometry, assumed simulation inputs) is run and tested; `campus_50` and `tiny_campus_block` are kept for reference only
- Optional `--misting-count 0|4|6|10` loads nested USC placements from `misting_scenarios.json` without copying the map; omission retains original actuators. Every stage records the explicit selection in `diagnostics.misting_scenario`.
- Misting uses a 10 m radius. `main.load_simulation_graph` cuts walkways at every mister's circle (`utils/graph/mist_zones.py`), so only the inside piece gets the full 10.8 F; the rest gets none. No stacking or distance decay. Environment also outputs uncooled air temperature and humidity, Heat Exposure turns them into `uncooled_utci_c`, and Control switches misters on at 35 C and off below 33 C of that uncooled UTCI. Scenario startup validates anchors and rejects touching/overlapping circles.
- Coordinates: local `x_m` and `y_m` values in meters
- Automated model and graph utility tests live under `tests/`; see `tests/README.md` for coverage and known gaps

Run these smoke checks after making changes:

```powershell
python -m pip install -r requirements.txt
python main.py
python -m models._template.model
python -m unittest discover -s tests -t . -v
python -m compileall -q main.py utils models
```

## Read these files first

1. `README.md` — environment setup and common commands.
2. `main.py` — pipeline composition and execution.
3. `models/_template/README.md` — how to create a model.
4. `models/_template/model.py` — current model contract implementation.
5. `utils/pipeline/contracts.py` — shared `ModelInput` and `ModelOutput`.
6. `utils/graph/` — shared graph entities, graph state, and JSON loading.
7. `dataset/tiny_campus_block/README.md` — dataset schema and assumptions.
8. `models/verification/README.md` — final verification input contract.

## Project structure

```text
DynamicCoolCorridorSystem/
├── main.py                         # Pipeline entry point
├── requirements.txt               # Runtime dependencies
├── utils/
│   ├── graph/
│   │   ├── entities.py             # GraphNode, GraphEdge, and StaticActuator
│   │   ├── graph.py                # CampusGraph and property updates
│   │   ├── json_loader.py          # JSON parsing and validation
│   │   └── misting_scenarios.py    # Validated runtime misting overlays
│   └── pipeline/
│       └── contracts.py            # ModelInput and ModelOutput
├── models/
│   ├── _template/
│   │   ├── model.py                # Copyable model implementation
│   │   ├── config.json             # Human-editable settings
│   │   └── README.md               # Model creation guide
│   ├── environment/               # Weather, shade, and mist cooling
│   ├── heat_exposure/             # UTCI, exposure, and unsafe-heat flags
│   ├── control/                   # Misting and traffic-signal commands
│   ├── routing/                   # Persistent trip assignment and shared search
│   │   ├── demo/                  # Optional standalone visualizations
│   │   └── osm_build/             # Optional OSM rebuild/export tools
│   ├── pedestrian/                # Persistent movement, signal waits, occupancy
│   └── verification/
│       ├── model.py                # Project model and result assembly
│       ├── checks.py               # Python requirement checks
│       ├── strel_adapter.py        # Selected-route MoonLight adapter
│       ├── formula.mls             # Spatial route-safety formulas
│       ├── config.json             # Verification thresholds and outputs
│       ├── actuators.py            # Misting and signal/movement audit
│       └── README.md               # Formal input and integration contract
├── dataset/
│   ├── usc_osm_trimmed/            # Default: 832 nodes, 930 segments; misting layouts
│   ├── campus_50/                  # Synthetic 50-node grid and shade schedule
│   └── tiny_campus_block/
│       ├── dataset.json             # Dataset manifest and metadata
│       ├── nodes.json               # Canonical node data
│       ├── edges.json               # Canonical edge data
│       ├── actuators.json           # Canonical static actuator data
│       ├── tiny_campus_block.xlsx   # Human-readable mirror
│       └── README.md               # Dataset documentation
├── tests/                          # Model and shared graph utility tests
└── debug/                          # Ignored local model outputs
```

## Architecture

The code has three layers:

1. `utils` owns shared graph types, dataset loading, and pipeline contracts.
2. `models` owns model-specific configuration and calculations.
3. `main.py` loads the graph and runs model instances in order.

Models import `GraphNode`, `GraphEdge`, `CampusGraph`, `ModelInput`, and
`ModelOutput` from `utils` so all stages share the same types and validation.

The pipeline data flow is:

```text
dataset.json → nodes.json + edges.json + actuators.json
        ↓
load_campus_graph(...)
        ↓
ModelInput(graph)
        ↓
Model A adds one or more named dynamic properties
        ↓
ModelOutput.to_model_input()
        ↓
Model B receives the updated graph and all earlier properties
```

`CampusGraph`, `GraphNode`, and `GraphEdge` are frozen dataclasses. Property
updates create replacement graph objects instead of changing the original graph
in place.

## Static data and dynamic properties

Keep static facts in the JSON dataset:

- Node identifier, name, type, coordinates, accessibility, and description.
- Edge identifier, endpoints, distance, walking time, surface, and accessibility.
- Static actuator placement, type, graph references, and operating limits.

Keep changing or calculated values in `node.properties`, `edge.properties`, or
`actuator.properties`:

- Shade percentage
- Temperature
- Heat exposure
- Congestion
- Route cost
- Misting and LED status
- Active traffic-signal wait time
- Selected directional-LED edge

The loader initializes misting points and directional LEDs with `status: "off"`.
Traffic signals start with `status: "on"` and `wait_time_s` equal to their static
default; signal status must remain on. All actuators start with
`recommended_edge: null`; nonsignal actuators have `wait_time_s: null`.

Every edge is implicitly bidirectional. Shade is stored in runtime edge properties.

Edge and node dynamic property values must be finite numbers. Actuator properties
may be finite numbers, non-empty strings, or `null`. For every property it
reports, a model must produce exactly one value for every entity of its selected
target type.

## Shared pipeline contracts

`ModelInput` is a small envelope containing:

- `graph`: the current graph, including accumulated dynamic properties.
- `context`: optional runtime information that is not stored on graph entities.

`ModelOutput` contains:

- `model_name`: the stage identifier.
- `property_target`: `edge`, `node`, or `actuator`; state-only stages use `null`.
- `property_names`: generated property names in configured order, or an empty tuple.
- `property_values`: one complete entity-ID-to-value mapping per generated property,
  or an empty mapping for a state-only stage.
- `graph`: the updated graph containing every generated property.
- `diagnostics`: optional debugging or monitoring details.

The contract does not require a model to generate a property. When properties
are present, it validates unique non-empty names, a non-null target, exact
agreement between `property_names` and `property_values`, complete target
coverage, numeric edge/node values, and actuator values that are finite numbers,
non-empty strings, or `null`. A state-only model returns an updated graph with
`property_target=None`, `property_names=()`, and `property_values={}`.

`ModelOutput.to_model_input()` passes the updated graph to the next model.
Properties exist in memory until the process ends. `save_json()` writes the full
state for debugging. See the [pedestrian limitations](models/pedestrian/README.md#current-limits)
for persistence support.

`main.py` preserves supplied runtime context and builds a fresh
`context["stage_diagnostics"]` mapping keyed by stage model name each pass.
Before Pedestrian it captures `context["pedestrian_before_movement"]`.
The final verifier preserves `diagnostics["pedestrian_state"]` and adds
`diagnostics["verification"]`. Complete edge counts travel with the graph.

## Creating a model

1. Copy `models/_template` to a descriptive lower-case folder.
2. Rename `ModelTemplate` and `ModelConfig` to domain-specific names.
3. Edit the copied `config.json`.
4. Implement `calculate_edge()`, `calculate_node()`, or `calculate_actuator()`.
5. Keep shared graph and pipeline classes imported from `utils`.
6. Run the model directly from the repository root.
7. Import and instantiate it in `build_pipeline()` in `main.py`.

For example, the current initialization stages in `main.py` are shown below.
This excerpt uses `Path` and `PipelineStage` already imported or defined there:

```python
from models.environment import EnvironmentModel
from models.heat_exposure import HeatExposureModel


def build_initialization_pipeline(dataset_directory: Path | None = None) -> list[PipelineStage]:
    return [
        EnvironmentModel.from_config(),
        HeatExposureModel.from_config(),
    ]
```

The list contains model instances, not model classes. Execution order is
significant: a model must appear after every stage that produces properties it
consumes.

Environment supplies shade. The round builder `build_pipeline(pedestrians,
trip_requests=None, dataset_directory=None)` adds Control before these stages,
then Routing, the supplied persistent Pedestrian simulator, and Verification.
Preserve that order and the shared simulator when registering another stage.

Every model must implement this compatible interface:

```python
def predict(self, model_input: ModelInput) -> ModelOutput:
    ...
```

## Model configuration

Each model owns its configuration type and `config.json`. Shared input and output
contracts do not constrain model-specific parameters.

Required template configuration fields:

| Field | Meaning |
| --- | --- |
| `model_name` | Stable identifier used in output and debug filenames |
| `description` | Plain-language model description |
| `enabled` | Enables or disables the stage |
| `property_target` | `edge`, `node`, or `actuator` |
| `output_properties` | Property names produced by the model, or an empty list for a state-only model |
| `input_properties` | Earlier properties to consume, or an empty list |
| `parameters` | Model-specific numeric parameters |

`from_config()` is a convenience constructor. It loads and validates the model's
configuration before returning the model instance.

## Dataset rules

The JSON files are the canonical runtime inputs. The tiny dataset's workbook
is a human-readable mirror. For generated datasets, make reproducible changes
in the generator or exporter and regenerate the JSON: use
`dataset/campus_50/generate_campus_50.py` for campus_50 and
`models/routing/osm_build/export_pipeline.py` for the USC base map.
The USC misting overlay is configured separately in `misting_scenarios.json`.

When changing the dataset:

1. For tiny_campus_block, edit the JSON first and refresh its workbook afterward.
   For generated datasets, update the generating code and regenerate the JSON.
2. Keep node and edge identifiers unique.
3. Ensure every edge endpoint references an existing node.
4. Use distinct endpoints for every edge.
5. Keep edge distances positive.
6. Allow a blank `location_name` only when a node has no recognized place name,
   such as an intersection.
7. Update the affected dataset README; update the tiny dataset's schema guide
   as well when the shared schema changes.
8. Ensure actuator anchors and referenced edges exist in the graph.

Current edge record fields:

```text
edge_id, from_node, to_node, distance_m, walk_time_min, surface_type, accessible
```

Current node record fields:

```text
node_id, location_name, location_type, x_m, y_m, accessible, description
```

## Running and debugging

Run the configured pipeline:

```powershell
python main.py
```

Use a different compatible dataset:

```powershell
python main.py --dataset path\to\dataset
```

Save every stage for inspection:

```powershell
python main.py --debug-dir debug
```

The `debug/` directory is ignored by Git. Debug JSON includes the static graph,
all accumulated properties, every result from the current stage, and diagnostics.

## Dependency management

The project intentionally uses `requirements.txt` rather than `pyproject.toml`.
`pythermalcomfort` for the UTCI heat calculation.
`MoonLight 0.3.1` for the verification model.

When adding a third-party runtime dependency:

1. Add it to `requirements.txt`.
2. Explain why it is needed in the related change or documentation.
3. Install with `python -m pip install -r requirements.txt`.
4. Prefer the standard library when it meets the requirement.

## Integration and limitations

Pipeline ordering is maintained manually in `build_pipeline()`. Reported outputs
are validated, but input-property dependencies are checked during execution
rather than before the pipeline starts.

Current limitations are described with the affected behavior:

- [Routing policy and estimates](models/routing/README.md#verification-policy-and-remaining-limitations).
- [Pedestrian movement and persistence](models/pedestrian/README.md#current-limits).
- [Verification scope](models/verification/README.md#engines-and-actuator-scope).

Use pipeline smoke runs and the automated suite under `tests/` for validation.

## Suggested GPT workflow

Give a new GPT session this prompt:

```text
Work in the DynamicCoolCorridorSystem repository. Read HANDOFF.md, README.md,
main.py, models/_template/README.md, and the relevant files under utils before
editing. Follow the architecture documented in HANDOFF.md and import shared
graph and pipeline classes from utils.
Treat dataset.json, nodes.json, edges.json, and actuators.json as canonical static data and keep
dynamic values in graph properties or model state. Record any new runtime
package in requirements.txt and explain its purpose. Maintain the
existing tests under tests/ and update tests/README.md when coverage changes.
Inspect the current Git status before editing,
preserve unrelated changes, and smoke-check the result with python main.py,
python -m models._template.model, and compileall. Run relevant automated checks;
the full suite command is python -m unittest discover -s tests -t . -v.
My requested change is:

<describe the task here>
```

When asking GPT to create a model, also specify:

- Whether it targets nodes, edges, or actuators.
- The properties it consumes, if any.
- The properties it produces.
- The formula or desired behavior.
- Required configuration parameters.
- Where it belongs in pipeline order.


## Routing / pedestrian integration

Read models/routing/README.md and dataset/usc_osm_trimmed/README.md. Routingc's
unchanged search/cost functions now live in models/routing/routing_core.py;
both prototype and pipeline import them. The pipeline graph view supplies those
functions with heat-derived walkability, not the prototype's placeholder values.
No OSMnx is required at runtime. STREL is registered only in the final verification audit.
Optional standalone demos live in models/routing/demo; OSM source data and
rebuild/export tools live in models/routing/osm_build. See their READMEs.

build_pipeline registers Routing immediately before the persistent simulator.
Routing calls assign_route for pending requests once and retains unmatched
requests for later retries. Pedestrian reports complete edge pedestrian_count
and per-agent diagnostic snapshots. IDs/progress persist; do not reconstruct
the simulator or router in the round loop. Routing is a model-specific handoff
to the existing simulator; shared graph/contracts remain in utils.

The real USC graph preserves every source walkway, splitting geometry into
unique node chains to avoid ambiguous parallel node routes. Its own building
lookup is loaded by main.py; Environment uses the selected dataset directory to
read source-walkway grouping and generate shade. USC has no fixed shade schedule.
Shade, weather, infrastructure
and accessibility assumptions must be disclosed; they are not surveyed data.

--trips accepts origins/destinations and preference. In main.py, legacy --routes
is interpreted as demand endpoints and recomputed; direct simulator fixed-route
APIs are unchanged. See the routing README for matching dataset commands.

Environment time stays fixed with --time and follows the clock with --time-range;
--pedestrian-dt advances walking and signal waiting.
Signal waits execute at controlled departures; see the model guides for current
movement and control behavior.

## Current-condition verification

Verification is registered after Pedestrian and audits current upstream evidence.
If any available safe route exists, selection
must be safe. Otherwise it must minimize maximum heat exposure rate, then
time-weighted cumulative exposure, then travel time, with no hard heat ceiling.
The 2.5x arrival bound is independent and never filters route candidates.

The verifier audits pre-movement routes and post-movement remaining routes,
including partial-edge progress and captured signal countdowns. It considers all
resolved destination entrances and shares a committed current-edge prefix across
comparisons. Efficient Dijkstra passes replace exhaustive simple-path search.
Environment and Heat Exposure expose real same-round diagnostic evidence.
Misting physics and signal/movement accounting are audited. Read the verifier
README for input contracts, result codes, scope, and commands. Routing uses a
comfort-weighted cost and can fail the separately audited route policy.

## Persistent signal countdowns

Pedestrian movement samples the latest traffic-signal wait_time_s before leaving
its anchor on a controlled edge. Countdown fields in agent.py persist across
rounds, and same-timestep time left after release is used for walking.
WAITING without a countdown still means an externally paused agent. Walking
time keeps its original meaning; waiting is separate, with derived elapsed
time exposed in snapshots. Never reconstruct or reroute an agent after its
wait begins. Routing cost/estimates currently exclude signal delay.

See models/pedestrian/README.md for explicit assumptions and run
models/routing/example_signal_trips.json to exercise a real-map signal. Extend
the existing tests under tests/pedestrian for this behavior. Shared graph,
Environment, Heat Exposure, Control, and Verification checks also live under
tests/. Signal positions remain hypothetical.
