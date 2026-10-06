# Dynamic Cool Corridor System

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
        - graph/ (shared nodes, edges, graph state, and CSV loading)
        - pipeline/ (shared model input and output contracts)
    - models/
        - _template/ (copyable, human-editable model starter)
    - dataset/
        - tiny_campus_block/ (9-node fictional test graph)
    - main.py

## Creating a model

Start with [`models/_template`](models/_template/README.md). Each model keeps
human-tunable values in a small `config.json` file and exposes a consistent
Python input/output interface.

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
stage.

## Testing dataset

[`dataset/tiny_campus_block`](dataset/tiny_campus_block/README.md) contains a
small campus graph with node coordinates, location categories, walkable edges,
distances, and accessibility information.
