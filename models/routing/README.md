# Routing model and pedestrian simulation

Owner: Umar

The other models work out how hot every walkway is. This one answers the
question a pedestrian actually has: **"I'm here, I want to go there, and I care
this much about comfort versus speed. Which way should I walk?"** It also
simulates many people walking at once, so we can see what comfort-aware
routing does for a crowd.

## Quick start

From the repository root:

```powershell
python main.py                                   # full pipeline, routing runs last
python -m models.routing.model                   # self-checks + demo (placeholder heat)
python -m models.routing.dijkstra                # Dijkstra correctness check only

python -m pip install matplotlib pillow          # only needed for pictures
python -m models.routing.visualize               # one trip at 3 preferences -> routes.png
python -m models.routing.visualize --heat-test   # proof that comfort routing works
python -m models.routing.simulate                # crowd simulation, results table
python -m models.routing.simulate --animate      # ...plus simulation.gif
```

Pictures and animations go in `routing_output/` (ignored by git).
`matplotlib` and `pillow` are deliberately **not** in `requirements.txt`. They're
only for drawing, and the pipeline itself stays standard-library only.

Useful options for `simulate` and `visualize`:

| Option | Meaning |
| --- | --- |
| `--time 15:00` | clock time (default 13:00) |
| `--scenario mild` | `mild`, `hot`, `extreme_heat`, `humid` (from the Environment model) |
| `--forecast oracle` | `current`, `oracle`, `noisy` (see "Placeholders") |
| `--agents 25` | `simulate` only: number of people |
| `--block-at 0` | `simulate` only: minute the busiest walkway closes; 0 = never |
| `--seed 7` | `simulate` only: a different random crowd |
| `--start`, `--dest` | `visualize` only: place names, e.g. `"USC Village"` |

## Where this fits

```text
dataset/campus_50  ──load──►  CampusGraph
                                   │
  initialization:      Environment ─► Heat Exposure
  each round:  Control ─► Environment ─► Heat Exposure ─► Routing
                                                             │
                     per-edge properties (blocked, planned_pedestrians)
                     + every planned route in diagnostics (see --debug-dir)

  separately:  python -m models.routing.simulate
               runs one pipeline round, then walks a crowd second by second
```

What routing reads, and from whom:

| Needs | From | Field |
| --- | --- | --- |
| Walking time per edge | dataset | `walk_time_min`, `distance_m` |
| How hot each edge is | Heat Exposure (Zabari) | edge `heat_exposure_rate`, `unsafe_heat` |
| Crosswalk wait times | Control (Kai-Ming) | traffic-signal `wait_time_s` at its `anchor_node` |
| Mister on/off (pictures only) | Control | misting-point `status` |
| Place names, positions | dataset | `location_name`, `x_m`, `y_m` |
| Time of day, scenario | runtime | `context["time"]`, `context["scenario"]` |

What it writes on **every** edge each round:

| Property | Meaning |
| --- | --- |
| `blocked` | 1.0 if the walkway is closed this round, else 0.0 |
| `planned_pedestrians` | how many of this round's planned trips use the edge |

## The files, in reading order

| File | What it does |
| --- | --- |
| `dijkstra.py` | The search. Knows nothing about heat; asks a cost function what each step costs. |
| `costs.py` | Where heat numbers come from (real or placeholder) and the cost formula. |
| `forecast.py` | "How hot will this edge be when I get there?" Placeholders for the missing prediction model. |
| `model.py` | `Planner` (answers route requests) and `RoutingModel` (the pipeline stage). |
| `simulate.py` | People walking, waiting at crosswalks, replanning around closures. |
| `visualize.py` | Route pictures and the animation. |
| `config.json` | Every tunable value. |

## The cost formula

Dijkstra needs a cost where **lower is better**. For each walkway step:

```text
minutes    = walk_time_min  (+ crosswalk wait if we walk INTO a signal, unless it's our destination)
discomfort = min(1, heat_exposure_rate / 20)                  # 0 = comfortable, 1 = extreme
cost       = minutes × (1 + K × preference × discomfort)      # K = 4
```

- **preference 0** makes the heat term vanish, giving the plain fastest route.
- **Time is never dropped**, even at preference 1. Otherwise a perfectly shaded
  walkway would cost zero, and the router would send people on endless shaded
  detours.
- **K = 4** means that at preference 1, the hottest possible walkway "feels"
  5 times as long as it is. That caps how far a comfort-seeker will detour.
- **Why divide by 20?** `heat_exposure_rate` is UTCI degrees above
  comfortable (26 °C). 26 + 20 = 46 °C is where the official UTCI scale begins
  "extreme heat stress", so discomfort reaches 1 there.

**Worked example.** A 60 m walkway takes 0.71 min. At 13:00 on a hot day its
`heat_exposure_rate` is 14 → discomfort 14 / 20 = 0.7.
- Speed-seeker (preference 0): cost = 0.71
- Balanced (0.5): 0.71 × (1 + 4 × 0.5 × 0.7) = **1.70**
- Comfort-seeker (1): 0.71 × (1 + 4 × 0.7) = **2.70**

A tree-lined walkway of the same length at rate 9 (discomfort 0.45) costs a
comfort-seeker 0.71 × 2.8 = 1.99. So they'd accept a detour of up to about 35%
extra time to swap the hot walkway for the shaded one.

Crosswalk waits come from the Control model's live settings, so when it
shortens waits at hot crossings, routes respond automatically.

### Why the scale is 20 and not 12 (a measured decision)

The handoff suggested 12 (the gap between comfortable 26 °C and the 38 °C
unsafe line). Measured on `campus_50`, that **saturates on hot afternoons**:
most edges are past the unsafe line, so they all score 1 and look equally bad.
Comfort then changed only 3% of routes in extreme heat. With 20, nothing
saturates within the scenarios the Environment model offers.

There's also a `"relative"` mode (`discomfort_mode` in config), which scores
each edge between today's coolest (0) and hottest (1). It **backfires**: it
treats small heat differences as huge and sends people on long detours, and
since exposure grows with every minute walked, the detours barely help or even
hurt. Comfort-seekers avoided only 0.2% of heat on a hot afternoon, and got
5.4% *more* heat in extreme heat, while walking 12–14% longer. It's kept only
for experiments.

## Key result: what comfort-aware routing achieves

Measured with `simulate` (comfort-seekers compared with their own fastest route):

| Conditions | Heat avoided | Extra time |
| --- | --- | --- |
| Mild, 13:00 | **about 26%** | about 5% |
| Hot, 13:00 | about 0–3% | about 0–1% |
| Extreme heat, 15:00 | about 1–2% | under 1% |

**The router isn't the bottleneck; the heat data is.** On hot days, the
coolest walkway is only about 40% less hot than the hottest (rate ~8.4 vs ~14.4),
so a shaded detour rarely saves enough heat to pay for its extra minutes in the
sun. On mild days, shade makes a much bigger relative difference, and routing
pays off. This is worth discussing with the team: stronger shade effects in the
Environment / Heat Exposure models would make routing matter more on exactly
the days it's meant for.

## How Dijkstra handles "go to a building"

`resolve_nodes` turns a place name into a **set** of nodes, because a building
can have several entrances. Dijkstra finalizes nodes in order of increasing
cost, so the first destination node it finalizes is the cheapest one, and it
stops there. In `campus_50` every place is one node, but nothing changes for
a dataset with several entrances per building.

Correctness is checked against Floyd-Warshall, a completely different
algorithm (`python -m models.routing.dijkstra`).

## The simulation

- Each person gets a random start and destination place, a random preference
  in [0, 1], and a departure time in the first 3 minutes (all from `--seed`).
- Time moves in 5-second steps. A person's position is **(which route step,
  meters along that walkway)**, so moving means adding meters. x/y is only
  worked out for drawing.
- Walking into a signalized crossing (not the destination) means waiting
  `wait_time_s` there, still collecting heat at the rate of the walkway they
  arrived on.
- At `--block-at` minutes, the walkway most people are about to use closes.
  Everyone with it ahead **replans from the end of the walkway they're on**
  (no U-turns mid-walkway). People waiting at a crossing replan from where
  they stand. No route at all means `stuck`.
- Each walkway is walked in exactly its dataset `walk_time_min`, so the
  simulation and the planner agree to the second.
- The final table compares each person with **their own fastest route**:
  "heat −26%, time +5%" means 26% less heat for 5% more time.

## Placeholders (things that don't exist yet)

Every missing input sits behind a small swappable piece, so real data can
replace it later without touching the router.

| Missing piece | Placeholder | To replace it |
| --- | --- | --- |
| Heat values (only when routing runs on its own) | `PlaceholderHeat`: fake values built from the dataset's shade schedule. Always prints a WARNING. | Run routing after Heat Exposure (it does in `main.py`). |
| Heat **forecast** (prediction model) | `forecast_mode`: `current` = no forecast; `oracle` = perfect forecast by rerunning Environment + Heat Exposure at the future time; `noisy` = oracle + error growing 2 °C per hour ahead. | Implement `rate_at(edge_id, minutes_ahead)` and return it from `make_forecast`. |
| Crowd density | Not in the cost. `make_step_cost` already accepts a `crowd` argument, with a commented line showing where the term goes. `planned_pedestrians` is the data it would read. | Add the term, plus a `crowd_weight` in config. |

The forecast only affects **planning**: the simulation charges people the heat
of the current round. Trips last a few minutes, so this barely matters, but a
multi-hour simulation would need the Environment model rerun as time passes.

## Configuration (`config.json`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `comfort_weight_k` | 4.0 | at preference 1, the hottest edge feels (1 + K)× as long |
| `discomfort_scale_c` | 20.0 | UTCI degrees above comfortable that count as maximum discomfort |
| `unsafe_penalty_min` | 0.0 | extra minutes added to `unsafe_heat` edges, scaled by preference |
| `random_trips` | 10 | random trips planned per pipeline round (plus `demo_trips`) |
| `seed` | 42 | makes random trips repeatable |
| `forecast_noise_c_per_hour` | 2.0 | error growth for the `noisy` forecast |
| `heat_source` | `pipeline` | `pipeline` (falls back to placeholder if missing) or `placeholder` |
| `forecast_mode` | `current` | `current`, `oracle`, `noisy` |
| `discomfort_mode` | `absolute` | `absolute` or `relative` (not recommended, see above) |
| `blocked_edges` | `[]` | closed walkways, e.g. `["E047"]` |
| `demo_trips` | 4 trips | fixed trips planned every round |

## Glossary

- **UTCI** (Universal Thermal Climate Index): "feels like" temperature in °C,
  combining air temperature, humidity, sun, and wind. 9–26 °C is no heat
  stress; 26–32 moderate; 32–38 strong; 38–46 very strong; above 46 extreme.
- **heat_exposure_rate**: UTCI minus 26 °C (never below 0). How much heat
  stress you're under per minute on that walkway.
- **Heat exposure (degree-minutes)**: rate × minutes, summed along a route.
  10 minutes at rate 5 = 50 degree-minutes, the same as 5 minutes at rate 10.
- **unsafe_heat**: 1.0 when UTCI ≥ 38 °C (the Heat Exposure model's threshold).

## Decisions made beyond the handoff document

| Decision | Alternative rejected | Why |
| --- | --- | --- |
| Discomfort scale 20 °C | 12 °C (handoff default) | 12 saturated on hot days (measured above) |
| Added `forecast.py` | Forecast code inside `costs.py` | Kept each file short and single-purpose |
| Simulation walks each edge in its dataset `walk_time_min` | Fixed 1.4 m/s | Dataset times are rounded; a fixed speed drifted ±0.2% from the planner |
| Removed `walk_speed_mps` from config | Keep it | Nothing used it after the change above |
| Trip time measured from the first tick after departure | From the scheduled departure | Otherwise up to 5 s of the time step counted as walking |
| Closure in the demo at minute 2 | Minute 4 | Most campus_50 trips finish in 2–4 minutes; at minute 4 few people are left to replan |
| A building with several entrances leaves from its first entrance | Try every entrance | campus_50 has one node per place; easy to extend later |
| Lazy imports in `__init__.py` | Direct import | Same pattern as the team's other models; avoids a Python warning with `python -m` |
| Demo trips naming places a dataset lacks are skipped with a note | Crash | `python main.py --dataset dataset/tiny_campus_block` must keep working |
