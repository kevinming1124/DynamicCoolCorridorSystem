# Dynamic Cool Corridor System

New developers and GPT sessions should begin with [HANDOFF.md](HANDOFF.md).

## Python version

- 3.12

## Environment setup

Create and activate a virtual environment, then install the project
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The requirements file is currently empty because the project uses only the
Python standard library. Add third-party runtime packages to it when they are
introduced by the code.

## Project structure

- DynamicCoolCorridorSystem/
    - utils/
        - graph/ (shared nodes, edges, actuators, graph state, and JSON loading)
        - pipeline/ (shared model input and output contracts)
    - models/
        - _template/ (copyable, human-editable model starter)
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

The dataset is loaded once. Initialization runs Environment -> Heat Exposure
to supply the controller's inputs. Each round then runs Control -> Environment
-> Heat Exposure, carrying the updated graph into the next round. By default,
one round runs after initialization. Choose a positive number of rounds:

```powershell
python main.py --rounds 5 --time 13:00 --scenario hot --debug-dir debug
```

Time and scenario stay fixed across rounds. Each round's environment applies
the latest misting commands, then heat exposure is recalculated. Final heat
results reflect the final round's misting state. Signals and LEDs reflect the
control decision made at the start of that round.

Debug files are grouped under `initialization/` and `round_001/`, `round_002/`,
and so on. The completion output lists final actuator states.

`build_initialization_pipeline()` defines the initialization stages;
`build_pipeline()` defines one round. `run_pipeline()` executes a single pass,
and `run_simulation()` initializes and repeats the rounds. Each stage receives
the graph produced by the previous stage, including accumulated properties.

## Testing dataset

[`dataset/tiny_campus_block`](dataset/tiny_campus_block/README.md) contains a
small campus graph with node coordinates, location categories, walkable edges,
distances, accessibility information, static controllable infrastructure, and
loader-derived initial actuator state.
