# campus_50 dataset

Owner: Aman (Environment Model)

This is the full-size campus used by the Dynamic Cool Corridors simulation:
**50 nodes, 100 edges, 4 misting points, and 6 pedestrian traffic signals.**
It uses exactly the same JSON format as `dataset/tiny_campus_block`, so the
shared loader in `utils/graph` reads it without any code changes. Place names
come from the USC University Park campus and are arranged roughly like the real
campus, but the grid is simplified and not to scale. Coordinates are local
meters, not latitude and longitude.

The small `tiny_campus_block` dataset is untouched and is still the default.

## Quick start

Run these from the repository root.

Rebuild the dataset (only needed after changing the generator):

```powershell
python dataset/campus_50/generate_campus_50.py
```

Run the pipeline on this dataset:

```powershell
python main.py --dataset dataset/campus_50
```

Expected output: the pipeline completes and reports results for 100 edges.

## Files

| File | What it holds |
| --- | --- |
| `generate_campus_50.py` | Script that creates every file below. Edit settings at the top, then rerun. |
| `dataset.json` | Manifest, same format as the tiny dataset. |
| `nodes.json` | 50 locations: 15 named campus places and 35 path intersections. |
| `edges.json` | 100 walking paths with distance and walking time. |
| `actuators.json` | 4 misting points and 6 traffic signals. |
| `shade_schedule.json` | Shade percent for every edge at every 30-minute step from 08:00 to 18:00. |

The generator is deterministic (fixed seed 42), so rerunning it gives
identical files. It uses only the Python standard library.

## How the campus is laid out

Nodes sit on a 5 x 10 grid. Columns are 60 m apart (west to east) and rows are
50 m apart (south to north). Node IDs run `N01` to `N50`, starting at the
southwest corner and moving east, then north.

Edges are made in three groups:

| Group | Count | Edge IDs |
| --- | --- | --- |
| East-west paths | 45 | `E001`-`E045` |
| North-south paths | 40 | `E046`-`E085` |
| Diagonal shortcuts | 15 | `E086`-`E100` |

Walking time uses 1.4 m/s, the same speed as the tiny dataset. Every edge is
bidirectional, as the project requires.

Named places (rough real arrangement, north at the top):

| Area | Places |
| --- | --- |
| North | USC Village, Webb Tower |
| North-center | Leavey Library, McCarthy Quad, Taper Hall |
| Center | Ronald Tutor Hall, Doheny Memorial Library, Tommy Trojan, Ronald Tutor Campus Center |
| South-center | Lyon Center, Alumni Park, Seeley G. Mudd Building |
| South | Parkside, Everybody's Kitchen (EVK), Expo Park/USC Metro Station |

See `nodes.json` for exact node IDs and coordinates.

## Actuators

**Misting points** (`A01`-`A04`) sit at McCarthy Quad, Alumni Park, Ronald
Tutor Campus Center, and the Expo Park/USC Metro Station. Each can cool by up to 5.4 F. Following the
shared actuator rules, misting points are tied to a location, not to edges. The
environment model decides which edges a mister cools based on distance from the
mister's coordinates.

**Traffic signals** (`A05`-`A10`) sit at the six most connected intersections.
Each controls every edge touching its intersection, with a 30 s default wait
and limits of 10 s to 90 s. The control model (Kai-Ming) changes these waits at
runtime.

No directional LED signs are included, because they are not part of the
current project plan.

## Shade schedule

Computing real shadows from building shapes and sun angles is out of scope.
Instead, every edge gets one of four **shade profiles**, and each profile lists
a shade percent for every 30-minute time slot.

| Profile | Meaning | Shade during the day |
| --- | --- | --- |
| `open` | No buildings or trees nearby | 5% all day |
| `tree_lined` | Trees along the path | 60% all day |
| `building_east` | Building on the east side | 70% until 10:30, 40% at 11:00-11:30, then 15% |
| `building_west` | Building on the west side | 15% until 13:00, 40% at 13:30-14:00, then 70% |

Profiles are assigned with one simple rule:

- North-south paths run beside buildings. Even columns get `building_east`
  (morning shade); odd columns get `building_west` (afternoon shade).
- East-west paths on rows 1 and 3 are `tree_lined`; the others are `open`.
- Diagonal shortcuts cross lawns and plazas, so they are `open`.

Current totals: 42 open, 18 tree-lined, 20 building-east, 20 building-west.

To look up shade for an edge at a given time:

```python
import json

shade = json.load(open("dataset/campus_50/shade_schedule.json"))
profile = shade["edges"]["E046"]               # e.g. "building_east"
percent = shade["profiles"][profile]["10:00"]  # e.g. 70.0
```

### Why shade is a separate file

The project rules say shade is a dynamic value and must not be a static column
in `edges.json`. Keeping it in `shade_schedule.json` leaves `edges.json` in the
standard format. The shared loader ignores this file; only the environment
model reads it.

## Checks built into the generator

The generator stops with an error if any of these fail:

- exactly 50 nodes and 100 edges
- no duplicate edges between the same two nodes
- every node reachable from every other node
- every edge has a shade profile
- every profile covers all 21 time slots with values from 0 to 100

The shared loader then applies the project's own rules (unique IDs, valid
endpoints, no self-loops, positive distances, valid actuators) when you run
`main.py`.

## Changing the dataset

Edit the settings near the top of `generate_campus_50.py` (grid size, spacing,
named places, mister locations, number of signals, or shade profiles), then
rerun the script. Do not hand-edit the JSON files, because the next run will
overwrite them.

No Excel mirror is included, because writing `.xlsx` files needs a third-party
package and the project is currently standard-library only.

## What uses this dataset next

The environment model (`models/environment/`) reads this graph and the shade
schedule and produces these properties for every edge at each 30-minute step
(see `models/environment/README.md`):

| Property | Meaning |
| --- | --- |
| `air_temp_f` | Air temperature after any misting cooling |
| `humidity_pct` | Relative humidity after any misting increase |
| `solar_radiation_wm2` | Sun strength in W/m2 |
| `shade_pct` | Shade percent from `shade_schedule.json` |
| `mist_cooling_f` | Cooling from nearby misters (0 when none are on) |

Wind is intentionally not modeled, per the course feedback.
