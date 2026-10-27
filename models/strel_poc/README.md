# STREL proof of concept

This standalone model proves that the project's Python environment can load a
MoonLight STREL script, convert `CampusGraph` into MoonLight arrays, run a
spatial monitor, and return its verdicts through the shared `ModelOutput`
contract.

It intentionally is not registered in `main.py`. The proof of concept uses one
fixed temperature snapshot and this Boolean formula:

```text
true reach(distance_m)[0, 80] (air_temperature_f < 90.0)
```

For each campus node, the formula asks whether a node cooler than 90 F is
reachable within 80 meters. The left side is `true`, so intermediate nodes do
not have an additional constraint.

Run it from the project root:

```powershell
.\.venv\Scripts\python.exe -m models.strel_poc.model
```

The command checks the MoonLight verdicts against expected results and exits
with an error if the parser, Java bridge, graph conversion, or formula result is
incorrect.

## Deliberate limitations

- Uses one fixed time snapshot rather than a temporal history.
- Uses node temperature and edge distance, but not edge heat exposure.
- Uses Boolean semantics rather than quantitative robustness.
- Is not registered in the production pipeline.

These boundaries keep the experiment small. The production model can reuse the
validated graph and signal conversion while adding real upstream properties,
configuration, temporal data, and pipeline registration.
