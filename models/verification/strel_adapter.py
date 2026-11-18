"""Evaluate current-condition safety on the selected ordered route only."""
from pathlib import Path
from moonlight import ScriptLoader
from utils import GraphEdge


class RouteSafetyMonitor:
    def __init__(self, formula_path: str | Path, formula_name: str):
        self.formula_path = Path(formula_path)
        self.formula_name = formula_name
        self._monitor = None

    def evaluate(self, route: tuple[GraphEdge, ...]) -> bool:
        if not route:
            raise ValueError("MoonLight cannot monitor an empty route")
        if self._monitor is None:
            script = ScriptLoader.loadFromFile(str(self.formula_path.resolve()))
            if self.formula_name not in tuple(script.getMonitors()):
                raise ValueError(f"Missing MoonLight formula: {self.formula_name}")
            self._monitor = script.getMonitor(self.formula_name)
        values = [[[
            1.0 if edge.properties["unsafe_heat"] == 0.0 else 0.0,
            1.0 if index == len(route) - 1 else 0.0,
        ]] for index, edge in enumerate(route)]
        adjacency = [[float(i), float(i + 1), 1.0] for i in range(len(route) - 1)]
        # MoonLight 0.3.1's Python wrapper always prints the full graph/signal.
        # Its Java numeric-array entry point returns the same Boolean-domain
        # verdicts without dumping route data on every audit.
        result = self._monitor.scriptComponent.monitorToDoubleArrayAdjacencyList(
            [0.0], [adjacency or [[0.0, 0.0, 0.0]]], [0.0], values)
        if len(result) != len(route) or len(result[0]) != 1 or len(result[0][0]) != 2:
            raise ValueError("Unexpected MoonLight result shape")
        verdict = float(result[0][0][1])
        if verdict not in (-1.0, 1.0):
            raise ValueError(f"Non-Boolean MoonLight verdict: {verdict}")
        direct = all(edge.properties["unsafe_heat"] == 0.0 for edge in route)
        if (verdict == 1.0) != direct:
            raise RuntimeError("MoonLight and direct route-safety checks disagree")
        return direct
