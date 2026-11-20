# Control model

The controller reads the current graph and returns a replacement graph with
updated actuator properties. In `main.py`, initialization runs Environment ->
Heat Exposure once, followed by `--rounds` repetitions of Control -> Environment
-> Heat Exposure -> Routing -> Pedestrian -> Verification (default: one round).
Each round carries its graph forward;
time and scenario stay fixed unless `--time-range` advances the clock (see the root README). Final heat results reflect the last misting
commands, while signal settings use the heat inputs at that round's start.

## Inputs and observation area

- Heat Exposure supplies **edge** `uncooled_utci_c` (UTCI as if no mister were
  on) for misters, and `unsafe_heat`, a numeric flag of 0.0 or 1.0, for signals.

Each mister inspects every walkway segment with positive-length overlap inside
its circular cooling zone, including nearby segments not connected to its anchor.
The radius comes from Environment's `mist_range_m` (currently 10 m), and uses the
same intersection calculation as cooling. Tangency, boundary-only contact, and
zero-length segments do not count.

A mister switches on a dead band of the hottest observed `uncooled_utci_c`,
set in this model's `config.json`:

| Current state | Switches when | Default |
| --- | --- | --- |
| off | hottest uncooled UTCI >= `mist_on_utci_c` -> on | 35 C |
| on | hottest uncooled UTCI < `mist_off_utci_c` -> off | 33 C |

Between the two thresholds a mister keeps its current state. Both thresholds
sit below Heat Exposure's unsafe line (38 C UTCI), so misters start before a
walkway becomes unsafe. Using uncooled heat means a mister's own cooling cannot
make its zone look safe and switch it off; without that, it would flicker on
and off every round. On a hot 08:00-17:00 day with four misters, each switches
on once, between 10:00 and 11:00, and stays on. Missing or non-numeric values
produce an error. A zone with no observed segments also produces an error.

The pipeline cuts walkways at the circle boundary before the run, so the
observed segments are exactly the pieces inside the circle.

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
