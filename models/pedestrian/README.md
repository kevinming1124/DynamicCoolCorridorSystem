# Pedestrian pipeline interface

`PedestrianSimulator.predict(ModelInput) -> ModelOutput` is the pipeline entry
point. The name follows the shared interface; this stage simulates movement,
not a heat or crowd forecast. Existing `step(dt)` and `get_state()` remain usable.
Call either `step` or `predict` for an interval, not both, to avoid moving twice.

## Input

- `graph`: the latest CampusGraph, including upstream dynamic properties and
  actuator state. Existing agents and elapsed simulation time are retained.
- `context["pedestrian_dt_sec"]`: optional finite, nonnegative timestep in
  seconds; defaults to 1.0. Zero publishes occupancy without advancing time.
- No heat or temperature property is required. Properties are preserved without
  renaming, converting, or using them for route decisions.

## Output

Every edge receives `pedestrian_count`, the number of non-arrived pedestrians
assigned to that edge at the end of the step. Empty edges receive zero. Waiting
agents count on their assigned edge, including at position zero; this is an
explicit occupancy convention, not a physical node-queue model. Arrivals are
excluded. This is an instantaneous count, not throughput, density, or a forecast.
Node-pair routes work in either direction. Ambiguous parallel active edges raise
an error rather than silently attributing occupancy to the wrong edge.

`diagnostics["pedestrian_state"]` contains the existing individual snapshot and
simulation time. Movement, graph replacement, and output creation are atomic:
a failed call retains the previous graph, agents, and simulation time.

## Example and downstream handoff

```python
from models.pedestrian.agent import Pedestrian
from models.pedestrian.model import PedestrianSimulator
from utils import ModelInput, load_campus_graph

graph = load_campus_graph("dataset/tiny_campus_block")
simulator = PedestrianSimulator(graph)
simulator.add_pedestrian(Pedestrian(1, "N01", "N03", ["N01", "N02", "N03"]))
context = {"pedestrian_dt_sec": 1.0, "time": "08:00"}
output = simulator.predict(ModelInput(graph=graph, context=context))

# Edge counts and upstream properties automatically travel with the graph.
# Individual snapshots require this explicit context handoff.
next_input = output.to_model_input(context={
    **context,
    "pedestrian_state": output.diagnostics["pedestrian_state"],
})
```

Reuse the same simulator for later cycles and pass each cycle's newest graph.
The snapshot is a copy; modifying it does not alter simulator agents. The
`simulation_time_sec` clock measures elapsed seconds. Its mapping to an absolute
time of day, movement cadence, and forecast horizon remain team decisions.

The default `main.py` still runs only the template. A pipeline owner can include
an initialized simulator in the stage list because it now exposes `predict`.
The existing runner forwards graph counts but drops context and diagnostics;
it must explicitly preserve context and attach the snapshot as above if a later
stage needs individual state. No shared runner or contract changes are included.

Not implemented: demand generation, heat-based route selection, rerouting,
signal waiting/release, LED-following, density, forecasts, or exposure tracking.
The branch remains independent of the unresolved edge `air_temp_f` versus node
`air_temperature_f` agreement. Control currently expects forecast fields that
`pedestrian_count` does not replace.
