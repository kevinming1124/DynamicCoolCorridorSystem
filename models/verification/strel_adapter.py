"""Adapt an ordered project route to a MoonLight STREL monitor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from moonlight import ScriptLoader

from utils import GraphEdge

from .checks import PREDICTED_EXPOSURE, edge_value


class RouteSafetyMonitor:
    """Evaluate the selected linear route at one exposure threshold."""

    def __init__(self, formula_path: str | Path, formula_name: str) -> None:
        self.formula_path = Path(formula_path)
        self.formula_name = formula_name

    def evaluate(self, route: tuple[GraphEdge, ...], threshold: float) -> bool:
        if not route:
            raise ValueError("MoonLight cannot monitor an empty route")

        signal_values = [
            [[
                edge_value(edge, PREDICTED_EXPOSURE),
                threshold,
                1.0 if index == len(route) - 1 else 0.0,
            ]]
            for index, edge in enumerate(route)
        ]
        adjacency = [
            [float(index), float(index + 1), 1.0]
            for index in range(len(route) - 1)
        ]
        if not adjacency:
            adjacency = [[0.0, 0.0, 0.0]]

        script = ScriptLoader.loadFromFile(str(self.formula_path.resolve()))
        available = tuple(script.getMonitors())
        if self.formula_name not in available:
            raise ValueError(
                f"Formula '{self.formula_name}' is missing from {self.formula_path}"
            )
        result: Any = script.getMonitor(self.formula_name).monitor(
            [0.0],
            [adjacency],
            [0.0],
            signal_values,
        )
        if len(result) != len(route):
            raise ValueError("MoonLight returned an unexpected location count")
        location_result = result[0]
        if len(location_result) != 1 or len(location_result[0]) != 2:
            raise ValueError("MoonLight returned an unexpected result shape")
        verdict = float(location_result[0][1])
        if verdict not in {-1.0, 1.0}:
            raise ValueError(f"MoonLight returned a non-Boolean verdict: {verdict}")

        direct_result = all(
            edge_value(edge, PREDICTED_EXPOSURE) <= threshold for edge in route
        )
        strel_result = verdict == 1.0
        if direct_result != strel_result:
            raise RuntimeError("MoonLight and direct route-safety checks disagree")
        return strel_result
