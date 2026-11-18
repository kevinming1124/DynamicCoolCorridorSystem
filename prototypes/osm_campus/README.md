# Real-campus prototype (OpenStreetMap)

Owner: Umar

An early, standalone prototype of comfort-vs-speed routing, built on the
**real USC University Park campus** walkway network from OpenStreetMap, rather
than the repository's synthetic `campus_50` grid.

It is **separate from the main pipeline**: nothing here is imported by
`main.py`, and it uses its own extra packages. The pipeline version of routing
lives in `models/routing/` on the `routinga` / `routingb` branches. This folder
is kept because it has the most realistic campus map in the project, which could
later become a dataset for the pipeline.

## What's here

| File | What it does |
| --- | --- |
| `map.osm` | OpenStreetMap export of the campus area (Jefferson, Vermont, Figueroa, Exposition) |
| `upc_graph.py` | Builds the walkway graph from `map.osm`: building **entrances** are nodes, walkways are split where a door meets them |
| `key_buildings.txt` | The 23 high-traffic buildings kept in the smaller "trimmed" graph |
| `router.py` | Hand-written Dijkstra with the comfort-vs-speed cost, closed walkways, routes to the nearest entrance |
| `sim.py` | Simulated pedestrians walking their routes and replanning around a closed walkway, saved as a GIF |

## Run it

From this folder, in its own virtual environment (it needs `osmnx`, which the
main project does not):

```powershell
cd prototypes\osm_campus
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python upc_graph.py                 # build the graphs (offline, a few seconds)
python router.py                    # one trip at 3 preferences -> routes.png
python router.py --heat-test        # comfort route avoids a scorching fastest route
python router.py --start "Taper Hall" --dest "Ginsburg Hall"
python sim.py                       # crowd simulation -> sim.gif
python sim.py --agents 25 --graph full
```

`upc_graph.py` must run first: `router.py` and `sim.py` read the graph files it
creates. Generated files (graphs, pictures, GIFs) are not committed; rerun
`upc_graph.py` to recreate them.

## The two graphs

| | Full | Trimmed |
| --- | --- | --- |
| Buildings | all 125 named in OpenStreetMap | the 23 in `key_buildings.txt` |
| Nodes / edges | about 340 / 443 | about 104 / 202 |
| Use | most realistic | closer to the project's planned scale |

Trimming removes dead-end paths that only led to dropped buildings, without
changing the distance of any route between kept buildings.

## Known limitations

- **Heat values are fake.** `router.py` and `sim.py` use smooth random
  "placeholder" walkability scores. There is no shade or heat model for this map
  yet. The pipeline's Environment and Heat Exposure models only cover
  `campus_50`.
- **Entrances are mostly guessed.** OpenStreetMap maps real doors for only 7 of
  125 buildings. The rest get one door on the side nearest a walkway. Real
  doors can be added in an `entrances_manual.csv` (columns
  `building,entrance,lat,lon`), which `upc_graph.py` reads automatically.
- **Some off-campus streets are included**, because the area is a rectangle.

## Making it a pipeline dataset (future)

Converting the trimmed graph into the repository's `nodes.json` /
`edges.json` format would let the real pipeline run on the real campus. That
also needs a shade schedule for its edges, which is the Environment model's
area, so it's a team decision.

Map data © OpenStreetMap contributors (ODbL).
