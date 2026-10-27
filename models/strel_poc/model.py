"""Run a small STREL reachability proof of concept with MoonLight."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from moonlight import ScriptLoader

from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph


PROJECT_ROOT: Final = Path(__file__).parents[2]
DEFAULT_DATASET_PATH: Final = PROJECT_ROOT / "dataset" / "tiny_campus_block"
DEFAULT_FORMULA_PATH: Final = Path(__file__).with_name("formula.mls")

INPUT_PROPERTY: Final = "air_temperature_f"
OUTPUT_PROPERTY: Final = "strel_cool_reachable"
FORMULA_NAME: Final = "cool_reachable"

# One deterministic snapshot for the proof of concept. Only N04 is below 90 F.
DEMO_TEMPERATURES_F: Final = {
    "N01": 90.3,
    "N02": 93.4,
    "N03": 95.4,
    "N04": 89.2,
    "N05": 94.6,
    "N06": 92.3,
    "N07": 97.0,
    "N08": 99.5,
    "N09": 98.1,
}

# N01, N02, N03, N04, N05, and N08 are at most 80 m from N04.
EXPECTED_REACHABLE: Final = {
    "N01": 1.0,
    "N02": 1.0,
    "N03": 1.0,
    "N04": 1.0,
    "N05": 1.0,
    "N06": 0.0,
    "N07": 0.0,
    "N08": 1.0,
    "N09": 0.0,
}


class STRELPocModel:
    """Evaluate one Boolean STREL formula on a single graph snapshot."""

    model_name = "strel_poc"

    def __init__(self, formula_path: str | Path = DEFAULT_FORMULA_PATH) -> None:
        self.formula_path = Path(formula_path)

    def predict(self, model_input: ModelInput) -> ModelOutput:
        graph = model_input.graph
        node_ids = tuple(graph.nodes)
        node_indexes = {node_id: index for index, node_id in enumerate(node_ids)}

        signal_values = self._build_signal_values(graph, node_ids)
        adjacency_list = self._build_adjacency_list(graph, node_indexes)

        script = ScriptLoader.loadFromFile(str(self.formula_path.resolve()))
        available_formulas = tuple(script.getMonitors())
        if FORMULA_NAME not in available_formulas:
            raise ValueError(
                f"Formula '{FORMULA_NAME}' is missing from {self.formula_path}"
            )

        monitor = script.getMonitor(FORMULA_NAME)
        raw_result = monitor.monitor(
            [0.0],
            [adjacency_list],
            [0.0],
            signal_values,
        )
        verdicts = self._extract_verdicts(node_ids, raw_result)
        updated_graph = graph.with_node_property(OUTPUT_PROPERTY, verdicts)

        return ModelOutput(
            model_name=self.model_name,
            property_target="node",
            property_names=(OUTPUT_PROPERTY,),
            property_values={OUTPUT_PROPERTY: verdicts},
            graph=updated_graph,
            diagnostics={
                "formula_name": FORMULA_NAME,
                "formula_path": str(self.formula_path),
                "semantics": "boolean",
                "moonlight_boolean_encoding": {"true": 1.0, "false": -1.0},
                "snapshot_time": 0.0,
                "node_indexes": node_indexes,
            },
        )

    @staticmethod
    def _build_signal_values(
        graph: CampusGraph, node_ids: tuple[str, ...]
    ) -> list[list[list[float]]]:
        values: list[list[list[float]]] = []
        for node_id in node_ids:
            value = graph.nodes[node_id].properties.get(INPUT_PROPERTY)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{node_id}: required numeric property "
                    f"'{INPUT_PROPERTY}' is missing"
                )
            values.append([[float(value)]])
        return values

    @staticmethod
    def _build_adjacency_list(
        graph: CampusGraph, node_indexes: dict[str, int]
    ) -> list[list[float]]:
        adjacency_list: list[list[float]] = []
        for edge in graph.edges:
            source = float(node_indexes[edge.from_node])
            target = float(node_indexes[edge.to_node])
            distance = float(edge.distance_m)

            # CampusGraph edges are undirected; MoonLight adjacency rows are directed.
            adjacency_list.append([source, target, distance])
            adjacency_list.append([target, source, distance])
        return adjacency_list

    @staticmethod
    def _extract_verdicts(
        node_ids: tuple[str, ...], raw_result: Any
    ) -> dict[str, float]:
        if len(raw_result) != len(node_ids):
            raise ValueError("MoonLight returned an unexpected location count")

        verdicts: dict[str, float] = {}
        for node_id, location_result in zip(node_ids, raw_result):
            if len(location_result) != 1 or len(location_result[0]) != 2:
                raise ValueError("MoonLight returned an unexpected result shape")
            moonlight_verdict = float(location_result[0][1])
            if moonlight_verdict not in {-1.0, 1.0}:
                raise ValueError(
                    "MoonLight returned a non-Boolean verdict: "
                    f"{moonlight_verdict}"
                )
            verdicts[node_id] = 1.0 if moonlight_verdict == 1.0 else 0.0
        return verdicts


def run_demo() -> ModelOutput:
    """Run the deterministic proof of concept and verify its expected verdicts."""
    graph = load_campus_graph(DEFAULT_DATASET_PATH)
    graph = graph.with_node_property(INPUT_PROPERTY, DEMO_TEMPERATURES_F)
    output = STRELPocModel().predict(ModelInput(graph=graph))

    verdicts = dict(output.property_values[OUTPUT_PROPERTY])
    if verdicts != EXPECTED_REACHABLE:
        raise AssertionError(
            f"Unexpected STREL verdicts: expected {EXPECTED_REACHABLE}, got {verdicts}"
        )
    return output


if __name__ == "__main__":
    result = run_demo()
    verdicts = result.property_values[OUTPUT_PROPERTY]
    print("\nSTREL proof of concept passed.")
    print("Formula: within 80 m, reach a node cooler than 90 F")
    for node_id, verdict in verdicts.items():
        label = "reachable" if verdict == 1.0 else "not reachable"
        print(f"  {node_id}: {label}")
