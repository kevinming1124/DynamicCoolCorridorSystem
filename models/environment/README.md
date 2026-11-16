# Environment model

Owner: Aman

This model creates the weather on the campus. For one time of day and one
weather scenario, it writes five values onto **every edge** of the campus
graph. Later models (heat exposure, prediction, routing, control) read these
values from the graph.

It was built from `models/_template` using the team workflow and uses only the
Python standard library. No shared files were changed.

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

These names and units are the model's fixed interface. Improvements will change
how the values are calculated, not what they are called.

## What it reads

| Input | Where it comes from | Default |
| --- | --- | --- |
| Time of day | `context["time"]`, as `"HH:MM"` | `"12:00"` |
| Weather scenario | `context["scenario"]` | `"hot"` |
| Which misters are on | each misting actuator's `status` on the graph | all off |
| Shade | `dataset/campus_50/shade_schedule.json` | 5% if an edge is not listed |

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

**Shade** is looked up in the shade schedule: the edge's profile (open,
tree-lined, building on the east, or building on the west) at the latest
30-minute slot at or before the current time. See `dataset/campus_50/README.md`.

**Misting** cools every edge within `mist_range_m` (75 m) of a mister that is
on. Cooling is strongest at the mister (up to its `max_heat_reduction_f`, 5.4 F)
and fades to zero at 75 m, measured to the closest point on the path. Where two
misters overlap, their cooling adds up but never goes past the strongest
mister's limit. Misting also raises humidity by up to
`mist_humidity_increase_pct` (5%), scaled the same way.

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

This prints the minimum, average, and maximum of each value across all 100
edges at the default time and scenario. Useful options:

```powershell
python -m models.environment.model --time 08:00
python -m models.environment.model --time 13:00 --scenario extreme_heat
python -m models.environment.model --time 13:00 --mister-on A01 A03
python -m models.environment.model --day
python -m models.environment.model --time 13:00 --save debug/environment_1300.json
```

`--mister-on` turns misters on for a quick test, the same way the control model
would. `--day` prints campus averages for every 30-minute step from 08:00 to
18:00. `--save` writes the complete output, including every edge, to a JSON
file. It also runs on the tiny dataset with
`--dataset dataset/tiny_campus_block` (every edge uses the default shade there).

## Use it from other code

```python
from models.environment import EnvironmentModel
from utils import ModelInput, load_campus_graph

graph = load_campus_graph("dataset/campus_50")
model = EnvironmentModel.from_config()

output = model.predict(
    ModelInput(graph=graph, context={"time": "13:00", "scenario": "hot"})
)

edge = output.graph.edges[0]
print(edge.edge_id, edge.properties["air_temp_f"], edge.properties["shade_pct"])
```

`output.diagnostics` also reports the time, scenario, which misters were on,
and how many edges used the default shade.

## Connecting it to the pipeline

The model is not yet added to `main.py`, because that file belongs to the
pipeline owner. To add it, import `EnvironmentModel` in `main.py` and put
`EnvironmentModel.from_config()` first in `build_pipeline()`. Running the whole
simulated day needs `main.py` to run the pipeline once per 30-minute step and
pass `{"time": ..., "scenario": ...}` as context; until then this model uses its
defaults.

## Files

| File | Purpose |
| --- | --- |
| `model.py` | The model (`EnvironmentModel`) and the command-line runner |
| `config.json` | Output names and tunable settings |
| `scenarios.json` | Weather scenarios |
| `__init__.py` | Makes `from models.environment import EnvironmentModel` work |

## Simplifications and planned improvements

This is a deliberately simple first version so the rest of the team can build
on it now. Possible improvements, which will not change the five outputs:

- Misting delay and fade-out over time (needs memory between time steps)
- Small differences in temperature between edges, such as hotter open plazas
- Shade-dependent surface heating
- More scenarios as the team needs them

Not modeled, by design: wind (removed per course feedback), heat risk scores
(heat exposure model), forecasts and observation noise (prediction model), and
signal wait times (control model).

To request a change to this model, contact Aman.
