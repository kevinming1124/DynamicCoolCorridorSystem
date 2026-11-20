# OSM campus build tools

Build the full and trimmed USC University Park walkway graphs from the bundled
`map.osm`, then export the trimmed graph for the main pipeline. Normal pipeline
runs use the existing exported JSON and do not need these optional tools.

## Build and export

From the repository root, using an environment with the optional dependencies:

```sh
python -m pip install -r models/routing/osm_build/requirements.txt
python models/routing/osm_build/upc_graph.py
python models/routing/osm_build/export_pipeline.py
python models/routing/osm_build/export_surroundings.py
```

`export_surroundings.py` needs only the standard library. It links each exported
segment back to `map.osm` by position and writes `surroundings.json` (surface tag
and distances to trees, green areas, buildings, parking, major roads and
fountains) for Environment's local temperature offsets. Rerun it after the
exporter.

Paths are resolved relative to the scripts, so they also work from other
directories. The builder reads the bundled OSM file offline. The exporter
overwrites the base JSON files in `dataset/usc_osm_trimmed`, including its
hypothetical actuators and source-walkway grouping. It does not generate a fixed
shade schedule; Environment generates shade. See the dataset README for details.

## Files and outputs

| File | Purpose |
| --- | --- |
| `map.osm` | Source campus map, bounded by Jefferson, Vermont, Figueroa and Exposition |
| `key_buildings.txt` | The 23 buildings retained in the trimmed graph |
| `upc_graph.py` | Build walkway graphs, split walkways at entrances, and render maps |
| `export_pipeline.py` | Convert the trimmed graph to the shared pipeline JSON format |
| `export_surroundings.py` | Record each segment's OSM surface and nearby features in `surroundings.json` |
| `requirements.txt` | Optional dependencies shared with the demos |

Generated files stay in this folder and are ignored by Git:

- `upc_walk_full.graphml` and `upc_walk_trimmed.graphml`.
- `buildings_full.json` and `buildings_trimmed.json` entrance lookups.
- `entrance_coverage.csv`, `upc_graph_full.png` and `upc_graph_trimmed.png`.

The full graph includes about 125 buildings and 340 nodes; the trimmed graph
includes 23 buildings and about 104 nodes. Trimming preserves route distances
between retained buildings.

Most entrances are inferred. To override them, add `entrances_manual.csv` here
with columns `building,entrance,lat,lon`, using exact OSM building names. An
override replaces all inferred/OSM entrances for that building. Inspect the
coverage report for names and entrance sources. Some off-campus streets are
included within the rectangular map boundary; accessibility is not surveyed.

Once built, run the [standalone demos](../demo/README.md) against these graphs.

Map data: [OpenStreetMap contributors (ODbL)](https://www.openstreetmap.org/copyright).
