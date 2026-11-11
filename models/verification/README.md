# Requirements verification model

This is the single final verification stage for each future pipeline round:

```text
Control -> Environment -> Heat -> Heat Predict -> Pedestrian -> Verification
```

The model is intentionally hybrid. MoonLight/STREL verifies the spatial safety
of the selected route. Python verifies bounded arrival, constrained coolest
fallback selection, and attributed actuator response. It does not generate or
modify routes, commands, environmental effects, or predictions.

The stage is implemented but is not registered in `main.py` because the real
upstream models do not yet provide its inputs.

## Requirements

1. **Route safety:** every selected edge is safe at its expected entry time.
2. **Bounded arrival:** selected time is no more than 2.5 times the fastest
   accessible path, without applying a safety constraint to the reference.
3. **Fallback:** when no bounded safe path exists, the selected usable fallback
   minimizes maximum exposure, then cumulative exposure, then travel time.
4. **Actuator response:** the same-round applied command has a bounded,
   attributable effect and the controlled value contains that effect.

The three outcomes are:

- `SAFE`: the normal-threshold route satisfies every requirement.
- `INFEASIBLE_FALLBACK`: no bounded normal-threshold path exists, but the
  constrained coolest fallback and relaxation are correct and usable.
- `INVALID`: one or more requirements failed.

## MoonLight boundary

`formula.mls` contains a helper formula, `segment_safe`, and the main
`safe_to_destination` formula. The adapter builds a linear MoonLight graph from
only the selected ordered route. This prevents a different safe campus path
from hiding an unsafe selected route.

The supplied exposure is already sampled at the expected entry time. This is a
spatial check of time-aware values, not continuous temporal monitoring.

## Formal upstream contract

Every edge must eventually contain these numeric properties:

| Producer | Properties |
| --- | --- |
| Pedestrian | `route_order`, `expected_entry_time_s`, `predicted_travel_time_s`, `fallback_selected`, `required_threshold_relaxation`, `pedestrian_demand` |
| Heat Predict | `predicted_exposure_at_entry`, `baseline_predicted_exposure_at_entry`, `misting_exposure_effect_at_entry` |
| Environment | `baseline_temperature_f`, `misting_temperature_effect_f`, `controlled_temperature_f`, `baseline_travel_time_s`, `signal_travel_time_effect_s` |

Exactly one node must have `route_origin = 1`, and exactly one must have
`route_destination = 1`. Selected edges use contiguous `route_order` values
starting at zero; other edges use `-1`. The round-level fallback flag and
relaxation are repeated on every edge because the shared output contract
requires complete property coverage.

Fallback verification requires comparable predicted exposure and travel-time
values for every candidate edge, not only selected edges. In the formal
pipeline, Heat Predict and Pedestrian must derive these from a common forecast
horizon. The temporary mocks supply one value per edge.

Control and Environment must leave these actuator properties:

| Type | Required properties |
| --- | --- |
| Misting point | `status`, `commanded_temperature_reduction_f`, `applied_temperature_reduction_f`, `effect_saturated`, `saturation_reason` |
| Traffic signal | `wait_time_s`, `applied_wait_time_s`, `effect_saturated`, `saturation_reason` |

Supported saturation reasons are configured. `physical_floor` requires local
temperature evidence at the configured floor. `no_demand` requires zero demand
on every controlled signal edge. A flag alone cannot excuse a missing effect.

Misting locality uses the minimum Euclidean distance from the actuator to the
edge segment. Signal locality uses the actuator's static `controlled_edges`.
The verifier checks attributed changes, not total environmental change.

## Outputs

The model adds these complete edge properties:

- `verification_selected_edge_safe` (`-1` means the edge is not selected)
- `verification_route_safe_normal`
- `verification_route_safe_applied`
- `verification_route_usable`
- `verification_bounded_arrival_valid`
- `verification_fallback_valid`
- `verification_actuator_response_valid`
- `verification_overall_valid`

Global Boolean results are repeated across edges. Detailed measurements,
optimal fallback information, engines, and violations are in diagnostics. The
next Pedestrian stage should use a route only when
`verification_route_usable = 1`.

## Mock demonstrations

Run from the project root:

```powershell
python -m models.verification.model
```

`mock_rounds.json` supplies explicit temporary upstream values for four cases:

- normal safe operation;
- a usable relaxed-threshold fallback;
- justified physical-floor saturation;
- an invalid non-local misting effect.

The mock loader is demo-only. `VerificationModel.predict()` never fills missing
production inputs.

## Future pipeline registration

After every upstream producer implements the contract, register this stage last:

```python
from models.verification import VerificationModel


def build_pipeline() -> list[PipelineStage]:
    return [
        ControlModel.from_config(),
        EnvironmentModel.from_config(),
        HeatModel.from_config(),
        HeatPredictModel.from_config(),
        PedestrianModel.from_config(),
        VerificationModel.from_config(),
    ]
```
