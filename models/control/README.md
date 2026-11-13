# Control model

The controller reads the current graph and returns a replacement graph with
updated actuator properties. It is intentionally not registered in `main.py`.

## Inputs and observation area

- Heat Exposure supplies **edge** `unsafe_heat`, a numeric flag of 0.0 or 1.0.
- Heat Prediction supplies edge `predicted_heat_exposure`.

Each mister inspects only edges connected to its `anchor_node`. It turns on
when any connected edge has `unsafe_heat == 1.0`, and off when all connected
edges have `unsafe_heat == 0.0`. It does not apply a separate temperature or
exposure threshold. The heat exposure model owns the decision threshold through
`unsafe_utci_c` (currently 38 degrees Celsius UTCI) in its configuration.
Temperature, humidity, solar radiation, shade, and assumed wind speed affect
that model's UTCI calculation. Every observed edge must have a numeric flag of
0.0 or 1.0; missing or invalid flags and anchors with no connected edges produce
an error.

Observation coverage is separate from cooling coverage: the controller does not
use the environment model's misting radius to select observed edges.

Traffic signals read `unsafe_heat` and `predicted_heat_exposure` on
`controlled_edges`. They use the minimum wait time when any controlled edge is
unsafe or the maximum predicted exposure is at least 0.7; otherwise they use the
default wait time. The 0.7 threshold applies only to predicted exposure.
Directional LEDs select the lowest predicted exposure among `recommended_edges`.

## Outputs

Misters write actuator `status` as `"on"` or `"off"`, which the environment model
already reads. Signals write `status` and `wait_time_s`; LEDs write `status` and
`recommended_edge`. Existing node and edge properties are preserved.

Pass the returned graph into environment to apply the commands. All stages must
use the same loaded dataset. Control still needs heat and prediction outputs;
environment output alone cannot run the full controller.

## Standalone demonstration

Run `python -m models.control.model` from the repository root. The demonstration
loads `campus_50`, runs `EnvironmentModel` with its default time and scenario,
then runs `HeatExposureModel` on the returned graph. It adds only synthetic
`predicted_heat_exposure` from `mock_model_outputs.json` before running control.
All real environment and heat exposure properties, including `unsafe_heat`, are
preserved by the mock loader.

The mock prediction values are independent of the environment conditions and
are only demonstration inputs. Integration should supply real prediction
outputs instead. This example runs one control decision; it does not advance
time or recalculate cooling after applying commands.
