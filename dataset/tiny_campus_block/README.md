# Tiny campus block dataset

This fictional dataset describes a walkable campus block as an undirected graph.
It is intentionally small enough for development examples.

## Files

- `nodes.csv` is the node table and the source for location coordinates.
- `edges.csv` is the edge table and connects node identifiers.
- `tiny_campus_block.xlsx` contains the same data in a convenient workbook.

The CSV files are the canonical machine-readable copies. If the workbook is
edited, export its Nodes and Edges sheets back to the matching CSV files before
running code against the dataset.

## Coordinate system

`x_m` and `y_m` are fictional local coordinates in meters. The origin `(0, 0)`
is the southwest corner of the test block. They are not latitude and longitude
and do not represent a real campus.

## Node fields

| Field | Meaning |
| --- | --- |
| `node_id` | Unique node identifier |
| `location_name` | Human-readable campus location |
| `location_type` | Machine-friendly location category |
| `x_m`, `y_m` | Local coordinates in meters |
| `accessible` | Whether the location is accessible |
| `description` | Short explanation of the location |

## Edge fields

| Field | Meaning |
| --- | --- |
| `edge_id` | Unique edge identifier |
| `from_node`, `to_node` | Endpoint node identifiers |
| `distance_m` | Straight-line endpoint distance |
| `walk_time_min` | Estimated time at 1.4 meters per second |
| `surface_type` | Path surface |
| `accessible` | Whether the segment is accessible |

Every edge is bidirectional. Treat each row as two directed connections when an
algorithm requires a directed graph. Time-varying values such as shade belong in
dynamic model properties rather than this static dataset.
An intersection may leave `location_name` blank when it has no commonly used
name. Use `location_type` set to `intersection` so applications can still label
or style the node appropriately.

This dataset has 9 nodes and 12 edges and is fully connected.
