# Environment model

Owner: Aman

This model creates the weather on the campus. For one time of day and one
weather scenario, it writes five values onto **every edge** of the campus
graph. Heat Exposure reads these values to calculate UTCI and heat flags;
Control and Routing consume those heat outputs. Verification audits the
environmental and heat evidence.

It was built from `models/_template` using the team workflow and uses only the
Python standard library. The pipeline passes the selected dataset folder in
`context["dataset_dir"]` and forwards Environment diagnostics to Verification.
The full pipeline also requires the packages in the root `requirements.txt`.

## What it outputs

Every edge gets these five properties:

| Property | Unit | Meaning |
| --- | --- | --- |
| `air_temp_f` | F | Air temperature on the path, **after** any misting cooling |
| `humidity_pct` | % | Relative humidity, **after** any misting increase |
| `solar_radiation_wm2` | W/m2 | Open-sky sunlight at this time (not reduced by shade) |
| `shade_pct` | % | How much of the path is in shade right now, 0-100 |
| `mist_cooling_f` | F | How much misters cooled this path (0 when none are on) |

Sunlight and shade are kept separate on purpose, so the heat exposure model can
decide how to combine them. The cooling from misters is already subtracted from
`air_temp_f`; `mist_cooling_f` is reported separately so its effect is easy to
see and evaluate.

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

**Misting** uses a circular zone with radius `mist_range_m` (10 m) around each
active mister. Full cooling applies inside the zone and zero outside; there is
no distance decay. The edge-average cooling contribution is
`max_heat_reduction_f * (covered length / total length)`. For example, 32%
coverage gives 32% of the mister's full cooling. Coverage uses the straight
segment between endpoint coordinates, independently of its stored travel distance.
Tangencies and zero-length segments contribute zero. Temperature and heat stress
remain edge averages rather than separate values inside/outside the zone.

Contributions add and retain the existing strongest-active-mister cap; this is
not a physical overlap model. Use nonoverlapping zones for the demonstration.
Misting also raises humidity by up to `mist_humidity_increase_pct` (5%), scaled
by the resulting cooling relative to the strongest active mister's full cooling.

At this stage, temperature, humidity, and sunlight are the same on every edge;
edges differ through shade and misting.

## Weather scenarios

Defined in `scenarios.json`:

| Scenario | Low / high temp (F) | Humidity (morning / afternoon) | Sky |
| --- | --- | --- | --- |
| `mild` | 62 / 80 | 70% / 40% | partly cloudy (0.8) |
| `hot` | 72 / 98 | 60% / 25% | clear (1.0) |
| `extreme_heat` | 80 / 110 | 45% / 15% | clear (1.0) |
| `humid` | 75 / 92 | 90% / 60% | mostly clear (0.9) |

To add a scenario, add an entry with the same five fields. No code changes are
needed.

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
file. It also runs on the tiny dataset with
`--dataset dataset/tiny_campus_block` (using automatic shade profiles there).

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
round, passing the selected dataset folder through `dataset_dir`. Time stays
fixed during a simulation run. Use the standalone `--day` option to inspect
shade and weather at successive times of day.

## Files

| File | Purpose |
| --- | --- |
| `model.py` | The model (`EnvironmentModel`) and the command-line runner |
| `config.json` | Output names and tunable settings |
| `scenarios.json` | Weather scenarios |
| `__init__.py` | Makes `from models.environment import EnvironmentModel` work |
