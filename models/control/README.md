# Control model

The controller reads the current graph and returns a replacement graph with
updated actuator properties. In `main.py`, initialization runs Environment ->
Heat Exposure once, followed by `--rounds` repetitions of Control -> Environment
-> Heat Exposure -> Routing -> Pedestrian -> Verification (default: one round).
Each round carries its graph forward;
time and scenario stay fixed. Final heat results reflect the last misting
commands, while signal settings use the heat inputs at that round's start.

## Inputs and observation area

- Heat Exposure supplies **edge** `unsafe_heat`, a numeric flag of 0.0 or 1.0.

Each mister inspects every walkway segment with positive-length overlap inside
its circular cooling zone, including nearby segments not connected to its anchor.
The radius comes from Environment's `mist_range_m` (currently 10 m), and uses the
same intersection calculation as cooling. Tangency, boundary-only contact, and
zero-length segments do not count. It turns on
when any observed edge has `unsafe_heat == 1.0`, and off when all observed
edges have `unsafe_heat == 0.0`. It does not apply a separate temperature or
exposure threshold. The heat exposure model owns the decision threshold through
`unsafe_utci_c` (currently 38 degrees Celsius UTCI) in its configuration.
Temperature, humidity, solar radiation, shade, and assumed wind speed affect
that model's UTCI calculation. Every observed edge must have a numeric flag of
0.0 or 1.0; missing or invalid flags produce an error. A zone with no observed
segments also produces an error.

An overlapping segment's heat flag represents the whole segment, even if only
part lies inside the radius. This is the current spatial approximation.

Traffic signals read `unsafe_heat` on `controlled_edges`. They use the minimum
wait time when any controlled edge has `unsafe_heat == 1.0`; otherwise they use
the default wait time. Signals always stay on.
Legacy directional LED records pass through unchanged and are excluded from
rule checks.

## Outputs

Misters write actuator `status` as `"on"` or `"off"`, which the environment model
already reads. Signals write `status` and `wait_time_s`.
Existing node and edge properties are preserved.

Pass the returned graph into environment to apply the commands. All stages must
use the same loaded dataset. Control reads the current Heat Exposure outputs.

## Standalone demonstration

From the repository root:

```powershell
python -m models.control.model
python -m models.control.model --misting-count 6 --time 13:00 --scenario hot
python -m models.control.model --misting-count 10 --time 08:00 --scenario mild
python -m models.control.model --misting-count 0
python -m models.control.model --misting-count base
python -m models.control.model --save debug/control_rules.json
python -m models.control.model --dataset dataset/campus_50 --misting-count base
```

The default is `usc_osm_trimmed`, four misters, 13:00, and the `hot` scenario.
Layout options match the pipeline's 0/4/6/10 placement overlays. `base` uses the
dataset's original actuators (two misters for USC), and is also appropriate for
datasets without `misting_scenarios.json`. These options change only this run;
dataset files and the main pipeline's defaults are not modified.

The demo runs Environment -> Heat Exposure -> Control exactly once, passing
`dataset_dir` to Environment for consistent generated shade. It checks commands
against pre-control heat inputs. Misting observation is checked independently
using point-to-segment distance. Each report lists observed segments, unsafe
segment count, previous settings, expected command, actual command, and PASS/FAIL.
Failed checks give a nonzero command exit status.

There is no recalculation of cooling or examination of actuator effects in this
demo. Saved JSON contains commands, the unchanged pre-control heat properties,
layout metadata, and the rule-check report. Those heat properties describe
conditions before the commands, not their effects.
