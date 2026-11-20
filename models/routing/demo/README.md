# Standalone campus routing demos

Owner: Umar

Visualize comfort versus speed routing on the real USC University Park campus
walkway network. These demos share the pipeline's search and cost functions in
`../routing_core.py` but use placeholder walkability rather than pipeline heat
calculations. The main pipeline does not import these demos.

## Run

From the repository root, using an environment with the optional dependencies:

```sh
python -m pip install -r models/routing/demo/requirements.txt
python models/routing/osm_build/upc_graph.py
python models/routing/demo/router.py
python models/routing/demo/router.py --heat-test
python models/routing/demo/router.py --start "Taper Hall" --dest "Ginsburg Hall"
python models/routing/demo/sim.py
python models/routing/demo/sim.py --agents 25 --graph full
```

Build the graphs once before running the demos. Both demos read GraphML and
building lookups from `../osm_build`, regardless of the working directory.
They also support module execution from the repository root, for example
`python -m models.routing.demo.router --heat-test`.

## Files and outputs

| File | Purpose |
| --- | --- |
| `router.py` | Compare three comfort preferences and optionally close a walkway; save `routes.png` here |
| `sim.py` | Animate pedestrians and replanning around a closed walkway; save `sim.gif` here |
| `requirements.txt` | Install the optional dependencies shared with the OSM build tools |

Generated images and GIFs are ignored by Git. `--heat-test` makes the shortest
route hot to demonstrate comfort routing; `--block` demonstrates closure routing.
The simulation supports `--block-at 0` to disable the closure event.

Heat values in these demos are synthetic. Most building entrances are inferred,
and campus accessibility has not been surveyed. See the [OSM build tools](../osm_build/README.md)
for source files, manual entrance overrides and dataset rebuild instructions.

Map data: [OpenStreetMap contributors (ODbL)](https://www.openstreetmap.org/copyright).
