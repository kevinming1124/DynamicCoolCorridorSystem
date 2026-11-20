# Environment model

Owner: Aman

This model creates the weather on the campus. For one time of day and one
weather scenario, it writes eight values onto **every edge** of the campus
graph. Heat Exposure reads these values to calculate UTCI and heat flags;
Control and Routing consume those heat outputs. Verification audits the
environmental and heat evidence.

It was built from `models/_template` using the team workflow and uses only the
Python standard library. The pipeline passes the selected dataset folder in
`context["dataset_dir"]` and forwards Environment diagnostics to Verification.
The full pipeline also requires the packages in the root `requirements.txt`.

## What it outputs

Every edge gets these eight properties:

| Property | Unit | Meaning |
| --- | --- | --- |
| `air_temp_f` | F | Air temperature on the path, **after** any misting cooling |
| `humidity_pct` | % | Relative humidity, **after** any misting increase |
| `solar_radiation_wm2` | W/m2 | Open-sky sunlight at this time (not reduced by shade) |
| `shade_pct` | % | How much of the path is in shade right now, 0-100 |
| `mist_cooling_f` | F | How much misters cooled this path (0 when none are on) |
| `uncooled_air_temp_f` | F | Air temperature as if no mister were on |
| `uncooled_humidity_pct` | % | Relative humidity as if no mister were on |
| `local_temp_offset_f` | F | This walkway's fixed offset from its surface and surroundings, already included in both air temperatures |

Sunlight and shade are kept separate on purpose, so the heat exposure model can
decide how to combine them. The cooling from misters is already subtracted from
`air_temp_f`; `mist_cooling_f` is reported separately so its effect is easy to
see and evaluate. The two uncooled values let Heat Exposure report the heat
without misting, which Control uses to switch misters without reacting to their
own cooling.

These names and units define the current output interface.

## What it reads

| Input | Where it comes from | Default |
| --- | --- | --- |
| Time of day | `context["time"]`, as `"HH:MM"` | `"12:00"` |
| Weather scenario | `context["scenario"]` | `"hot"` |
| Which misters are on | each misting actuator's `status` on the graph | all off |
| Dataset folder | `context["dataset_dir"]` (`main.py` passes it) | optional, see Shade below |
| Shade | the loaded dataset's shade schedule, if it has one | automatic shade from edge direction |

The control model only needs to set a mister's `status` to `"on"`; this model
picks that up automatically. Other models do not call any special functions.

## How each value is calculated

All numbers mentioned here are settings in `config.json` or `scenarios.json`.

**Air temperature** follows a smooth daily curve. It is lowest around 03:00 at
the scenario's `temp_low_f`, rises through the morning, and peaks at
`temp_high_f` at 15:00 (`temp_peak_hour`). On the hot day this gives about 82 F
at 08:00, 96 F at 13:00, and 98 F at 15:00.

**Humidity** follows the same curve in reverse: highest when it is coolest
(`humidity_high_pct`) and lowest at the afternoon peak (`humidity_low_pct`).

**Solar radiation** is 0 before sunrise (06:00) and after sunset (20:00), rises
to `peak_solar_radiation_wm2` (900) at midday, and is multiplied by the
scenario's `cloud_factor` (1.0 for clear sky).

**Shade** works on any dataset. Each edge has one of four shade types, and each
type has a daily curve (`shade_profiles` in `config.json`):

| Type | Shade |
| --- | --- |
| `open` | 5% all day |
| `tree_lined` | 60% all day |
| `building_east` | 70% until 11:00, 40% until 12:00, then 15% (morning shade) |
| `building_west` | 15% until 13:30, 40% until 14:30, then 70% (afternoon shade) |

Where the shade type comes from, per edge:

1. **The dataset's own shade schedule**, if the edge is listed in it. The file
   is the one named `"shade_schedule"` under `"files"` in that dataset's
   `dataset.json` (campus_50 has one, see `dataset/campus_50/README.md`).
2. **Otherwise, automatically** from the edge's direction and position, using
   only node `x_m`/`y_m`:
   - north-south paths run beside buildings, alternating building-on-the-east
     and building-on-the-west from one path to the next;
   - every other east-west path is tree-lined, the rest are open;
   - diagonal paths cross lawns and plazas, so they are open.

   "Next path" uses the dataset's own spacing (typical edge lengths), so it
   works at any scale. On campus_50 this rule gives the same shade type as
   the hand-made schedule on all 100 edges.

So a new dataset needs no shade file to get simulated shade, a dataset that
grows new edges gets shade for them automatically, and campus_50's values are
unchanged.

USC has no fixed shade schedule: Environment generates shade using these
profiles. Its manifest also names `source_edges.json`, so each original
walkway receives one profile shared by all its geometry segments. Direction
and spacing are calculated from original walkway endpoints. These are
simulation assumptions, not shadows calculated from surveyed buildings or trees.

Pass `dataset_dir` when calling Environment to load any dataset schedule and
source-walkway grouping. Calls without it use automatic shade per graph edge;
there is no implicit campus_50 fallback. The output diagnostics report `shade_source`,
`edges_with_scheduled_shade`, and `edges_with_auto_shade`.

**Misting** cools only the part of a walkway inside a mister's circle (radius
`mist_range_m`, 10 m). Before the simulation starts, the pipeline cuts every
walkway where it crosses a mister's circle (`utils/graph/mist_zones.py`). An
82 m walkway with 11 m inside the circle becomes three pieces: the 11 m inside
piece and the two outside pieces. Pieces are named `<walkway>~1`, `~2`, ... in
walking order, and each keeps its walkway's shade profile.

Each edge then gets `max_heat_reduction_f` (currently 10.8 F) times the share
of the edge inside the circle. After cutting, that share is 1 for inside pieces
(full cooling) and 0 for the rest. There is no distance decay. Tangencies,
endpoint-only contact and zero-length segments count as outside. A graph that
was not cut (for example a hand-built test graph) gets the length-weighted
average instead.

The cuts use every mister on the map, on or off, so the graph stays the same
while misters switch during a run. When several active misters cover an edge,
the strongest contribution counts; misters do not stack. Misting also raises
humidity by up to `mist_humidity_increase_pct` (5%), scaled by the edge's
cooling relative to the strongest active mister's full cooling.

**Local temperature offset.** Each walkway gets a fixed air-temperature
offset from its surface and what is around it, so walkways do not all heat
up in lockstep. The facts come from OpenStreetMap: the dataset's
`surroundings.json` (built by `models/routing/osm_build/export_surroundings.py`)
records each segment's OSM `surface` tag and its distance to the nearest tree,
green area, building, parking lot, major road and fountain. The rules live in
`config.json` under `metadata.local_temperature`:

| Rule | Offset |
| --- | --- |
| Surface asphalt / paved / concrete or paving stones / untagged | +1.5 / +1.0 / +0.5 / 0 F |
| Tree within 10 m | -1.5 F |
| Grass or park within 10 m | -1.0 F |
| Fountain within 15 m | -0.5 F |
| Parking lot within 10 m | +1.0 F |
| Major car road within 10 m | +1.0 F |
| No building within 20 m (open plaza) | +1.0 F |

The total is clamped to -3..+3 F and added to the campus temperature on that
walkway (cut mist-zone pieces share their walkway's offset). On USC the
offsets range from -1.5 to +3 F, averaging about +0.7 F. On a hot day the share
of unsafe walkways now climbs over about 90 minutes (2% at 10:30, 29% at
11:00, 79% at 11:30, 91% at 12:00) instead of jumping from 0% to 92% in one
step. OSM maps only 38 individual trees, far fewer than are on campus, so tree
cooling is patchy. The values are modelling assumptions, not measurements.
A dataset without a surroundings file gets no offsets.

Humidity and sunlight are the same on every edge; edges also differ through
shade and misting.

## Weather scenarios

Defined in `scenarios.json`:

| Scenario | Low / high temp (F) | Humidity (morning / afternoon) | Sky |
| --- | --- | --- | --- |
| `mild` | 62 / 80 | 70% / 40% | partly cloudy (0.8) |
| `warm` | 70 / 90 | 60% / 25% | clear (1.0) |
| `hot` | 72 / 98 | 60% / 25% | clear (1.0) |
| `extreme_heat` | 80 / 110 | 45% / 15% | clear (1.0) |
| `humid` | 75 / 92 | 90% / 60% | mostly clear (0.9) |

To add a scenario, add an entry with the same five fields. No code changes are
needed.

`warm` is for watching misters switch both ways in one run. Near the misters,
uncooled UTCI reaches Control's 35 C on-threshold around 12:30, peaks near
36 C at 14:00, and drops below the 33 C off-threshold around 17:30:

```powershell
python main.py --time-range 08:00-18:00 --scenario warm --misting-count 4
```

On `hot` and hotter days the heat stays above 33 C until after 17:00, so
misters stay on through an 08:00-17:00 run.

## Run it on its own

From the repository root:

```powershell
python -m models.environment.model
```

The default dataset is `dataset/usc_osm_trimmed`. This prints the minimum,
average, and maximum of each value across all 930
edges at the default time and scenario. Useful options:

```powershell
python -m models.environment.model --time 08:00
python -m models.environment.model --time 13:00 --scenario extreme_heat
python -m models.environment.model --time 13:00 --mister-on SIM_M1 SIM_M2
python -m models.environment.model --day
python -m models.environment.model --time 13:00 --save debug/environment_1300.json
```

`--mister-on` turns misters on for a quick test, the same way the control model
would. `--day` prints campus averages for every 30-minute step from 08:00 to
18:00. `--save` writes the complete output, including every edge, to a JSON
file. The standalone run cuts walkways at mist circles the same way the pipeline does.

## Use it from other code

```python
from models.environment import EnvironmentModel
from utils import ModelInput, load_campus_graph

dataset_dir = "dataset/usc_osm_trimmed"
graph = load_campus_graph(dataset_dir)
model = EnvironmentModel.from_config()

output = model.predict(
    ModelInput(graph=graph, context={"dataset_dir": dataset_dir, "time": "13:00", "scenario": "hot"})
)

edge = output.graph.edges[0]
print(edge.edge_id, edge.properties["air_temp_f"], edge.properties["shade_pct"])
```

`output.diagnostics` also reports the uncooled temperature/humidity and misting
range/humidity settings as evidence for the final verification audit, plus the
time, scenario, which misters were on,
and how many edges used scheduled or automatic shade.

## Connecting it to the pipeline

`main.py` already runs Environment during initialization and each simulation
round, passing the selected dataset folder through `dataset_dir`. With
`--time`, time stays fixed during a run. With `main.py --time-range` (default
08:00-17:00), the runner sets `context["time"]` each round, refreshing every
`--env-update-min` minutes (default 30), so weather, sunlight and building shade
change through the day. Nothing changes inside this model: it still computes one
time per call. Use the standalone `--day` option to inspect shade and weather at
successive times of day without running the rest of the pipeline.

## Files

| File | Purpose |
| --- | --- |
| `model.py` | The model (`EnvironmentModel`) and the command-line runner |
| `config.json` | Output names and tunable settings |
| `scenarios.json` | Weather scenarios |
| `__init__.py` | Makes `from models.environment import EnvironmentModel` work |
