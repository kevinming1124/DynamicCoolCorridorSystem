# Dynamic Cool Corridor System

New developers and GPT sessions should begin with [HANDOFF.md](HANDOFF.md).

## Python version

- 3.12

## Java requirement

The MoonLight STREL engine requires Java 21 or newer and a `JAVA_HOME`
environment variable that points to the installed JDK.

## Environment setup

Create and activate a virtual environment, then install the project
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The project uses MoonLight 0.3.1 for STREL parsing and monitoring. The final
requirements verifier is implemented but is not registered until its upstream
models provide the formal inputs.

## Project structure

- DynamicCoolCorridorSystem/
    - utils/
        - graph/ (shared nodes, edges, actuators, graph state, and JSON loading)
        - pipeline/ (shared model input and output contracts)
    - models/
        - _template/ (copyable, human-editable model starter)
        - verification/ (final hybrid STREL and Python requirements verifier)
    - dataset/
        - tiny_campus_block/ (9-node fictional test graph)
    - main.py

## Creating a model

Start with [`models/_template`](models/_template/README.md). Each model keeps
human-tunable values in a small `config.json`, declares zero or more input
and output properties, and exposes the shared Python input/output interface.
Models that only update existing graph state may report no output properties.

Run the template example and checks from the project root:

```powershell
python -m models._template.model
```

## Running the pipeline

Run every configured model stage from the project root:

```powershell
python main.py
```

Save the complete output of each stage for debugging:

```powershell
python main.py --debug-dir debug
```

Add future model stages in execution order inside `build_pipeline()` in
`main.py`. Each stage automatically receives the graph produced by the previous
stage, including every dynamic property accumulated so far.

## Requirements verification demonstration

Run the final verifier with explicit mock upstream values:

```powershell
python -m models.verification.model
```

It verifies a normal route, constrained fallback, saturation, and invalid
actuator-locality scenarios. See
[`models/verification/README.md`](models/verification/README.md) for the formal
input contract and future pipeline placement.

## Testing dataset

[`dataset/tiny_campus_block`](dataset/tiny_campus_block/README.md) contains a
small campus graph with node coordinates, location categories, walkable edges,
distances, accessibility information, static controllable infrastructure, and
loader-derived initial actuator state.
