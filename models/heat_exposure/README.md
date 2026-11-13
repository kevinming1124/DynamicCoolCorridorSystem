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
python main.py --dataset dataset/campus_50 --time 13:00 --scenario hot
```

Use `--debug-dir debug` with `main.py` to save the environment and heat-stage
outputs for inspection.
