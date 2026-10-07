# Tiny campus block dataset

This fictional dataset describes a walkable campus block as an undirected graph.
It is intentionally small enough for development examples.

## Files

- `dataset.json` is the manifest and records dataset-wide metadata.
- `nodes.json` contains graph locations and coordinates.
- `edges.json` contains the walking connections between nodes.
- `actuators.json` contains fixed controllable infrastructure.
- `tiny_campus_block.xlsx` is a human-readable mirror of the JSON data.

The JSON files are canonical. Edit the JSON files, validate them by loading the
dataset, and then refresh the workbook mirror. Do not edit the workbook as an
independent source of truth.

`dataset.json` names the other files so a future dataset can use different
filenames without changing the loader.

## Coordinate system

`x_m` and `y_m` are fictional local coordinates in meters. The origin `(0, 0)`
is the southwest corner of the test block. They are not latitude and longitude
and do not represent a real campus.

## Node records

Each object in `nodes.json` contains:

| Field | Meaning |
| --- | --- |
| `node_id` | Unique node identifier |
| `location_name` | Human-readable campus location; may be blank |
| `location_type` | Machine-friendly location category |
| `x_m`, `y_m` | Local coordinates in meters |
| `accessible` | Whether the location is accessible |
| `description` | Short explanation of the location |

## Edge records

Each object in `edges.json` contains:

| Field | Meaning |
| --- | --- |
| `edge_id` | Unique edge identifier |
| `from_node`, `to_node` | Endpoint node identifiers |
| `distance_m` | Straight-line endpoint distance |
| `walk_time_min` | Estimated time at 1.4 meters per second |
| `surface_type` | Path surface |
| `accessible` | Whether the segment is accessible |

Every edge is bidirectional. Treat each record as two directed connections when
an algorithm requires a directed graph.

## Actuator records

Each object in `actuators.json` contains common fields plus a type-specific
`parameters` object:

| Field | Meaning |
| --- | --- |
| `actuator_id` | Unique actuator identifier |
| `actuator_type` | `misting_point`, `traffic_signal`, or `directional_led` |
| `position` | `anchor_node`, `x_m`, and `y_m` |
| `parameters` | Only the parameters used by this actuator type |
| `edge_relationships` | Edge links with `controls` or `recommends` semantics |
| `description` | Short explanation of the actuator |

Required type-specific parameters:

- `misting_point`: `cooling_radius_m` and `max_heat_reduction_c`
- `traffic_signal`: `default_wait_time_s`, `min_wait_time_s`, and
  `max_wait_time_s`
- `directional_led`: no static parameters at present

Traffic signals require at least one `controls` relationship. Directional LED
signs require at least one `recommends` relationship. Misting points do not
reference edges.

## Validation rules

- Node, edge, and actuator identifiers must be unique within their collections.
- Every edge endpoint and actuator anchor must reference an existing node.
- Every actuator edge relationship must reference an existing edge.
- Self-loop edges are not allowed and edge distances must be positive.
- Actuator parameters must exactly match the selected actuator type.
- Dynamic values such as shade, temperature, current misting intensity, active
  wait time, and displayed LED direction belong in runtime model state.

This dataset has 9 nodes, 12 edges, and 3 static actuators and is fully connected.
