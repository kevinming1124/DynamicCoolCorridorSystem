# Dynamic Cool Corridor System handoff

This document is the starting point for a new developer or a new GPT session.
It describes the current implementation, the decisions already made, and the
safe way to extend the project.

## Current state

- Python version: 3.12
- Third-party dependencies: none
- Dependency management: `requirements.txt`
- Active pipeline stages: one example model
- Dataset: fictional campus block with 9 nodes and 12 edges
- Coordinates: local `x_m` and `y_m` values in meters
- Automated unit tests: intentionally removed

Run these smoke checks after making changes:

```powershell
python -m pip install -r requirements.txt
python main.py
python -m models._template.model
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

## Project structure

```text
DynamicCoolCorridorSystem/
├── main.py                         # Pipeline entry point
├── requirements.txt               # Runtime dependencies
├── utils/
│   ├── graph/
│   │   ├── entities.py             # GraphNode, GraphEdge, and StaticActuator
│   │   ├── graph.py                # CampusGraph and property updates
│   │   └── json_loader.py          # JSON parsing and validation
│   └── pipeline/
│       └── contracts.py            # ModelInput and ModelOutput
├── models/
│   └── _template/
│       ├── model.py                # Copyable model implementation
│       ├── config.json             # Human-editable settings
│       └── README.md               # Model creation guide
├── dataset/
│   └── tiny_campus_block/
│       ├── dataset.json             # Dataset manifest and metadata
│       ├── nodes.json               # Canonical node data
│       ├── edges.json               # Canonical edge data
│       ├── actuators.json           # Canonical static actuator data
│       ├── tiny_campus_block.xlsx   # Human-readable mirror
│       └── README.md               # Dataset documentation
└── debug/                          # Ignored local model outputs
```

## Architecture

The code has three layers:

1. `utils` owns shared graph types, dataset loading, and pipeline contracts.
2. `models` owns model-specific configuration and calculations.
3. `main.py` loads the graph and runs model instances in order.

Models must depend on `utils`. Models must not define their own copies of
`GraphNode`, `GraphEdge`, `CampusGraph`, `ModelInput`, or `ModelOutput`.

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

The loader initializes a misting point and directional LED with `status: "off"`,
a traffic signal with `wait_time_s` equal to its static default, and an LED with
`recommended_edge: null`. Other actuator-type/state combinations use `null`.

Every edge is bidirectional by definition. Do not restore a `bidirectional`
column. Shade is dynamic and must not be restored as a static dataset column.

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
- `property_target`: `edge`, `node`, or `actuator`.
- `property_names`: every generated property name in configured order.
- `property_values`: one complete entity-ID-to-number mapping per property.
- `graph`: the updated graph containing every generated property.
- `diagnostics`: optional debugging or monitoring details.

The contract validates unique non-empty property names, exact agreement between
`property_names` and `property_values`, complete target coverage, numeric edge/node values, and actuator values that are
finite numbers, non-empty strings, or `null`.

`ModelOutput.to_model_input()` passes the updated graph to the next model.
Properties exist in memory until the process ends. `save_json()` writes the full
state for debugging, but the project does not currently reload a saved JSON state.

`main.py` currently resets `ModelInput.context` between stages. To preserve
context, change the handoff to:

```python
model_input = final_output.to_model_input(context=model_input.context)
```

## Creating a model

1. Copy `models/_template` to a descriptive lower-case folder.
2. Rename `ModelTemplate` and `ModelConfig` to domain-specific names.
3. Edit the copied `config.json`.
4. Implement `calculate_edge()`, `calculate_node()`, or `calculate_actuator()`.
5. Keep shared graph and pipeline classes imported from `utils`.
6. Run the model directly from the repository root.
7. Import and instantiate it in `build_pipeline()` in `main.py`.

Example pipeline registration:

```python
from models.heat_exposure.model import HeatExposureModel
from models.shade.model import ShadeModel


def build_pipeline() -> list[PipelineStage]:
    return [
        ShadeModel.from_config(),
        HeatExposureModel.from_config(),
    ]
```

The list contains model instances, not model classes. Execution order is
significant: a model must appear after every stage that produces properties it
consumes.

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
| `output_properties` | Non-empty list of property names produced by the model |
| `input_properties` | Earlier properties to consume, or an empty list |
| `parameters` | Model-specific numeric parameters |

`from_config()` is a convenience constructor. It loads and validates the model's
configuration before returning the model instance.

## Dataset rules

The JSON files are canonical. The workbook is a human-readable mirror.

When changing the dataset:

1. Edit the JSON files first and refresh `tiny_campus_block.xlsx` afterward.
2. Keep node and edge identifiers unique.
3. Ensure every edge endpoint references an existing node.
4. Do not create self-loop edges.
5. Keep edge distances positive.
6. Allow a blank `location_name` only when a node has no recognized place name,
   such as an intersection.
7. Update `dataset/tiny_campus_block/README.md` when the schema changes.
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
It currently contains no packages because the implementation uses only the
Python standard library.

When adding a third-party runtime dependency:

1. Add it to `requirements.txt`.
2. Explain why it is needed in the related change or documentation.
3. Install with `python -m pip install -r requirements.txt`.
4. Do not add a package when the standard library is sufficient.

## Decisions that should not be reversed casually

- The shared package is named `utils` by explicit project decision.
- Shared graph and pipeline classes live outside individual models.
- Coordinates use local meters (`x_m`, `y_m`), not latitude and longitude.
- Timestamps are intentionally absent.
- All edges are implicitly bidirectional.
- Shade is a dynamic property, not a static edge field.
- Models pass properties in memory through the updated graph.
- Debug output is optional and local.
- The project uses `requirements.txt` for now.
- The unit-test suite was intentionally removed. Do not recreate it unless the
  project owner requests it.

## Known limitations and next decisions

- Only the example distance-and-accessibility-cost model is registered.
- There is no persisted pipeline state loader.
- Pipeline ordering is maintained manually in `build_pipeline()`.
- The pipeline validates reported outputs but does not preflight input-property dependencies before execution.
- Runtime context is not preserved between stages by default.
- There is no packaging configuration or command-line installation entry point.
- Validation currently relies on smoke runs rather than an automated test suite.

## Suggested GPT workflow

Give a new GPT session this prompt:

```text
Work in the DynamicCoolCorridorSystem repository. Read HANDOFF.md, README.md,
main.py, models/_template/README.md, and the relevant files under utils before
editing. Preserve the existing architecture and decisions documented in
HANDOFF.md. Do not duplicate shared graph or pipeline classes inside models.
Treat dataset.json, nodes.json, edges.json, and actuators.json as canonical static data and keep
dynamic values in graph properties or model state. Do not add dependencies unless
necessary; record any new
runtime package in requirements.txt. Do not recreate the removed unittest suite
unless I explicitly request it. Inspect the current Git status before editing,
preserve unrelated changes, and smoke-check the result with python main.py,
python -m models._template.model, and compileall. My requested change is:

<describe the task here>
```

When asking GPT to create a model, also specify:

- Whether it targets nodes, edges, or actuators.
- The properties it consumes, if any.
- The properties it produces.
- The formula or desired behavior.
- Required configuration parameters.
- Where it belongs in pipeline order.

