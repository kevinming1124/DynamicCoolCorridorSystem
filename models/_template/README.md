# Human-editable model template

Use this folder as the starting point for any graph model in the system. It uses
only the Python standard library. Shared graph types, CSV loading, and pipeline
contracts come from the `utils` package.

## Create a model

1. Copy this `_template` folder into the relevant `models` category.
2. Rename the copied folder, using lower-case words separated by underscores.
3. Edit `config.json`. Most model tuning should happen there.
4. Set `property_target` to `edge` or `node`, and give `output_property` a clear
   name.
5. In `model.py`, rename `ModelTemplate` and change the formula in the clearly
   marked `HUMAN-EDITABLE SECTION` inside `calculate_edge` or `calculate_node`.
6. If the shared dataset schema grows, update the relevant class under
   `utils/graph` once rather than redefining it in this model.
7. Run the model before connecting it to the larger system.

For example, a heat-index model could live at
`models/HeatExposure/heat_index/`, with thresholds and weights in its copied
`config.json`.

## Configuration fields

| Field | Purpose |
| --- | --- |
| `model_name` | Stable name included in every result |
| `description` | Plain-language explanation of the model |
| `enabled` | Turns the model on or off |
| `property_target` | Chooses whether every edge or every node receives the result |
| `output_property` | Name stored on each target entity |
| `input_property` | Earlier property to consume, or `null` for the model's default input |
| `parameters` | Calculation weights a human can tune without changing code |
| `metadata` | Ownership, version, and notes; not used in calculations |

Keep JSON property names and text in double quotes. JSON does not support
comments, so put explanations in `description`, `metadata.notes`, or this file.

## Run the example

From the project root:

```powershell
python -m models._template.model
```

By default, the example loads `dataset/tiny_campus_block`. It validates unique
node and edge IDs, edge endpoints, numeric values, booleans, and distances before
calculating results.

The example cost begins with edge distance and adds a fixed penalty to
inaccessible segments. The result is saved as `distance_cost` on every edge.
Dynamic conditions such as shade can be generated as properties by an earlier
model stage and selected with `input_property`.

## Pass results to the next model

Every output contains the updated graph. Pass it directly to the next model:

```python
from utils import ModelInput, load_campus_graph

graph = load_campus_graph("dataset/tiny_campus_block")
first_output = first_model.predict(ModelInput(graph=graph))
second_input = first_output.to_model_input()
second_output = second_model.predict(second_input)
```

Set the second model's `input_property` to the first model's `output_property`.
Existing dynamic properties remain on the graph, so later models can use any
earlier result without changing the CSV dataset.

## Save output for debugging

Saving is explicit so normal model runs do not create files unexpectedly:

```python
from utils import ModelInput, load_campus_graph

graph = load_campus_graph("dataset/tiny_campus_block")
output = model.predict(ModelInput(graph=graph))
saved_path = output.save_json("debug/model_output.json")
```

The JSON file contains the generated property, diagnostics, all nodes and edges,
and every dynamic property accumulated on the graph. Saving to an existing path
replaces that file.

