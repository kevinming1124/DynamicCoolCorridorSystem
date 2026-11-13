# Control model

The controller reads the current graph and returns a replacement graph with
updated actuator properties. It is intentionally not registered in `main.py`.

## Inputs and observation area

- Environment supplies **edge** `air_temp_f` in degrees Fahrenheit.
- Heat supplies edge `heat_exposure`.
- Heat Prediction supplies edge `predicted_heat_exposure`.

Each mister inspects only edges connected to its `anchor_node`. It turns on
when the maximum `air_temp_f` on those edges is at least 95 F, or their maximum
`heat_exposure` is at least 0.7. The maxima may come from different edges.
Missing properties or an anchor with no connected edges produce an error.
Temperature is the environment model's edge value, not a point sensor reading.
If environment later uses spatial averages, the controller consumes those
averages without changing this interface.

Observation coverage is separate from cooling coverage: the controller does not
use the environment model's misting radius to select observed edges.

Traffic signals read current and predicted exposure on `controlled_edges`.
Directional LEDs select the lowest predicted exposure among `recommended_edges`.
These policies are unchanged.

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
then adds only synthetic `heat_exposure` and `predicted_heat_exposure` from
`mock_model_outputs.json` before running control. All environment properties,
including `air_temp_f`, are preserved by the mock loader.

These mock heat values are independent of the environment conditions and are
only demonstration inputs. Integration should supply real heat and prediction
outputs instead. This example runs one control decision; it does not advance
time or recalculate cooling after applying commands.
