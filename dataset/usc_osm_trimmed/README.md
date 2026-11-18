# USC OSM pipeline dataset

Exported from routingc's `prototypes/osm_campus/map.osm` via its existing
`upc_graph.py` trimmed graph. Attribution: OpenStreetMap contributors, ODbL;
see https://www.openstreetmap.org/copyright . This is a derived simulation
network, not a complete or surveyed USC accessibility map.

The source has 104 nodes, 202 walkways and 23 buildings. The shared dataset has
832 nodes and 930 segments. Geometry vertices become graph nodes so pedestrians
follow curved paths. Every original walkway is preserved, including parallel
walkways: edge-specific intermediate nodes make node routes unambiguous.
Segment distances sum to each source walkway's original length. Building
entrance order is preserved in buildings.json. source_edges.json maps all
exported segments back to original (u,v,key) edges for audit/reconstruction.
Coordinates are projected meters translated to a local origin; source CRS
and offset are recorded in dataset.json.

## Explicit simulation assumptions

- All edges have assumed 5% shade at all supplied time slots, not measured shade.
- Weather comes from the existing Environment scenario, not live observations.
- SIM_M1/SIM_M2 are hypothetical misters at Leavey/SGM; SIM_S1/SIM_S2 are
  hypothetical signals at high-degree junctions, not real installed hardware.
- Generic pedestrian traversability is assumed; accessibility is not surveyed.
- Most building entrances are inferred by the original routingc builder.

Do not apply campus_50's actuator positions or shade IDs to this map. The runner
loads the selected dataset's own schedule. A different comfort preference may
select the same route under these uniform shade assumptions.

## Rebuild (optional development dependencies)

Install prototypes/osm_campus/requirements.txt, run upc_graph.py inside that
folder to generate the trimmed GraphML/building lookup, then run
`python prototypes/osm_campus/export_pipeline.py` from the repository root.
The exported JSON files are sufficient for the main pipeline; it does not
import OSMnx, plotting, or the standalone prototype simulator.
