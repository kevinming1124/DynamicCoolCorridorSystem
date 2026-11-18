"""Heat forecasts: how hot will an edge be when the walker gets there?

The project plan includes a heat-prediction model that hasn't been built yet.
Its job, from routing's point of view, is one question:

    rate_at(edge_id, minutes_ahead) -> heat_exposure_rate at that future moment

Everything in routing asks the forecast, never the heat source directly. So
when the real prediction model exists, it only has to implement `rate_at`, and
nothing else in routing changes.

Until then, three stand-ins:

- CurrentForecast  ("current"): no forecast. Every future moment = right now.
- OracleForecast   ("oracle"):  PERFECT knowledge. Reruns the real Environment
                                and Heat Exposure models at the future clock time.
                                Shows the best a forecast could ever do.
- NoisyForecast    ("noisy"):   the oracle plus random error that grows the
                                further ahead you look, like a real forecast.
"""

from __future__ import annotations

import random
from typing import Mapping, Protocol

from utils import CampusGraph, ModelInput

from .costs import RATE_PROPERTY, HeatSource, clock_minutes


SLOT_MINUTES = 30  # the shade schedule and update cycle use 30-minute steps


class Forecast(Protocol):
    def rate_at(self, edge_id: str, minutes_ahead: float) -> float: ...


class CurrentForecast:
    """No forecast: assume conditions stay exactly as they are now."""

    def __init__(self, heat: HeatSource) -> None:
        self._heat = heat

    def rate_at(self, edge_id: str, minutes_ahead: float) -> float:
        return self._heat.rate(edge_id)


class OracleForecast:
    """Perfect knowledge of the future, by asking the real models directly.

    For a future moment, round the clock to the nearest 30-minute slot, run
    Environment -> Heat Exposure at that time (with today's misting settings),
    and read the heat rate. Results are cached per slot, so each slot is only
    computed once.
    """

    def __init__(self, graph: CampusGraph, context: Mapping[str, str], now: HeatSource) -> None:
        self._graph = graph
        self._scenario = context.get("scenario", "hot")
        self._now_min = clock_minutes(context.get("time", "12:00"))
        self._now = now
        self._cache: dict[int, dict[str, float]] = {}

    def _slot(self, minutes_ahead: float) -> int:
        """Minutes after midnight of the 30-minute slot nearest to now + ahead."""
        clock = self._now_min + minutes_ahead
        return int(round(clock / SLOT_MINUTES) * SLOT_MINUTES)

    def rate_at(self, edge_id: str, minutes_ahead: float) -> float:
        slot = self._slot(minutes_ahead)
        if slot == self._slot(0):
            return self._now.rate(edge_id)  # same slot as now: no need to rerun
        if slot not in self._cache:
            self._cache[slot] = self._run_models_at(slot)
        return self._cache[slot][edge_id]

    def _run_models_at(self, slot: int) -> dict[str, float]:
        # Imported here, not at the top, so routing doesn't load these models
        # unless an oracle forecast is actually used.
        from models.environment import EnvironmentModel
        from models.heat_exposure import HeatExposureModel

        slot = min(slot, 23 * 60 + 30)  # stay within the same day
        context = {"time": f"{slot // 60:02d}:{slot % 60:02d}", "scenario": self._scenario}
        environment = EnvironmentModel.from_config().predict(
            ModelInput(graph=self._graph, context=context)
        )
        heat = HeatExposureModel.from_config().predict(environment.to_model_input(context=context))
        return {e.edge_id: float(e.properties[RATE_PROPERTY]) for e in heat.graph.edges}


class NoisyForecast:
    """A realistic, imperfect forecast: the oracle plus growing random error.

    The error's typical size (standard deviation) grows linearly with how far
    ahead we look: `noise_c_per_hour` degrees per hour. It's seeded per edge and
    slot, so the same question always gets the same answer.
    """

    def __init__(self, oracle: OracleForecast, noise_c_per_hour: float, seed: int) -> None:
        self._oracle = oracle
        self._noise = noise_c_per_hour
        self._seed = seed

    def rate_at(self, edge_id: str, minutes_ahead: float) -> float:
        true_rate = self._oracle.rate_at(edge_id, minutes_ahead)
        sigma = self._noise * max(0.0, minutes_ahead) / 60.0
        if sigma == 0:
            return true_rate
        rng = random.Random(f"{self._seed}-{edge_id}-{round(minutes_ahead)}")
        return max(0.0, true_rate + rng.gauss(0.0, sigma))


def make_forecast(
    mode: str,
    graph: CampusGraph,
    context: Mapping[str, str],
    heat: HeatSource,
    noise_c_per_hour: float,
    seed: int,
) -> Forecast:
    """Build the forecast named in config."""
    if mode == "current":
        return CurrentForecast(heat)
    if heat.is_placeholder:
        print(f"NOTE: forecast_mode '{mode}' needs the real heat model; using 'current'.")
        return CurrentForecast(heat)
    if mode == "oracle":
        return OracleForecast(graph, context, heat)
    if mode == "noisy":
        return NoisyForecast(OracleForecast(graph, context, heat), noise_c_per_hour, seed)
    raise ValueError(f"forecast_mode must be 'current', 'oracle', or 'noisy', got '{mode}'")
