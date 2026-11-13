# Control model

The controller reads the current graph and returns a replacement graph with
updated actuator properties. It is intentionally not registered in `main.py`.

## Inputs and observation area

- Heat Exposure supplies **edge** `unsafe_heat`, a numeric flag of 0.0 or 1.0.
- Heat Exposure supplies edge `walking_heat_exposure` in degree-minutes.

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

Traffic signals read `unsafe_heat` on `controlled_edges`. They use the minimum
wait time when any controlled edge has `unsafe_heat == 1.0`; otherwise they use
the default wait time. Signals always stay on.
Directional LEDs select the lowest `walking_heat_exposure` among
`recommended_edges` and always stay on. If exposures tie, the first edge in
`recommended_edges` is selected.

## Outputs

Misters write actuator `status` as `"on"` or `"off"`, which the environment model
already reads. Signals write `status` and `wait_time_s`; LEDs write `status` and
`recommended_edge`. Existing node and edge properties are preserved.

Pass the returned graph into environment to apply the commands. All stages must
use the same loaded dataset. Control needs the heat exposure outputs;
environment output alone cannot run the full controller. No prediction inputs
are required.

## Standalone demonstration

Run `python -m models.control.model` from the repository root. The demonstration
loads `campus_50`, runs `EnvironmentModel` with its default time and scenario,
then runs `HeatExposureModel` on the returned graph before running control.
All decisions use real heat exposure outputs without mock prediction values.

This example runs one control decision; it does not advance
time or recalculate cooling after applying commands.
