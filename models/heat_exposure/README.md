# Heat exposure model

Owner: Zabari

This pipeline stage runs after `models/environment`. It reads environmental
properties from every campus edge and appends:

| Property | Meaning |
| --- | --- |
| `tmrt_c` | Simplified mean radiant temperature estimate, C |
| `utci_c` | Universal Thermal Climate Index, C |
| `heat_exposure_rate` | UTCI degrees above the configured reference per minute |
| `walking_heat_exposure` | Exposure accumulated while walking the whole edge, degree-minutes |
| `unsafe_heat` | 1.0 when UTCI meets/exceeds the configured unsafe threshold; otherwise 0.0 |

## Model assumptions

- UTCI is calculated by `pythermalcomfort` using wind at 10 m.
- Wind is constant across campus and configurable in `config.json`.
- The environment model has already included mist cooling in `air_temp_f`.
- Longwave radiant surroundings are approximated as air temperature.
- Solar load is reduced by the edge's shade percentage.
- Shortwave absorptivity, projected body area, and emissivity are configurable.
- The 1-10 route ranking and signal-wait accumulation belong to downstream
  routing/pedestrian models.

## Run

From the repository root:

```powershell
python -m pip install -r requirements.txt
python -m models.heat_exposure.model
python -m models.heat_exposure.model --time 08:00 --scenario mild
python -m models.heat_exposure.model --time 13:00 --mister-on SIM_M1 SIM_M2
python -m models.heat_exposure.model --save debug/heat_1300.json
python main.py --dataset dataset/usc_osm_trimmed --time 13:00 --scenario hot
```

The standalone run defaults to `dataset/usc_osm_trimmed`, 13:00, and the `hot`
scenario. It runs Environment first, passing the selected dataset folder so
USC's generated shade uses one profile per original walkway. The summary reports
minimum, average, and maximum heat values across all 930 segments.

Use `--dataset dataset/campus_50` or another compatible dataset to override the
map. Mister IDs must belong to the selected dataset and identify misting points;
USC uses `SIM_M1` and `SIM_M2`. Without `--mister-on`, loaded actuator states are
preserved. `--save` writes the full heat output, including environmental edge
properties and diagnostics recording the dataset, time, scenario, active
misters, and shade source. Use `--debug-dir debug` with `main.py` to save each
pipeline stage separately.

Heat calculations and thresholds are identical to the pipeline. More shade
reduces absorbed sunlight and the estimated radiant temperature under otherwise
identical conditions. Walking exposure scales with each segment's walking time;
sum it across a route to obtain total walking exposure. Signal waiting is
handled downstream.
