"""Regression checks for nested misting layouts and run metadata."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from utils import ModelInput, ModelOutput, load_campus_graph
from utils.graph.misting_scenarios import apply_misting_scenario

DATASET = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


class TestMistingScenarios(unittest.TestCase):
    def setUp(self):
        self.graph = load_campus_graph(DATASET)

    def test_nested_counts_preserve_map_and_signals(self):
        original = self.graph.as_dict()
        previous = set()
        for count in (0, 4, 6, 10):
            graph, metadata = apply_misting_scenario(self.graph, DATASET, count, 10)
            misters = {key: a for key, a in graph.actuators.items() if a.actuator_type == "misting_point"}
            self.assertEqual(len(misters), count)
            self.assertTrue(previous <= misters.keys())
            previous = set(misters)
            self.assertIs(graph.nodes, self.graph.nodes)
            self.assertIs(graph.edges, self.graph.edges)
            for key, actuator in self.graph.actuators.items():
                if actuator.actuator_type != "misting_point":
                    self.assertIs(graph.actuators[key], actuator)
            self.assertEqual(metadata["misting_count"], count)
            self.assertEqual(len(metadata["placements"]), count)
            self.assertEqual(metadata["mist_range_m"], 10)
            self.assertTrue(all(a.properties["status"] == "off" for a in misters.values()))
            self.assertTrue(all(a.max_heat_reduction_f == 5.4 for a in misters.values()))
        self.assertEqual(self.graph.as_dict(), original)

    def test_rejects_invalid_count_missing_config_anchor_and_overlap(self):
        for count in (2, True, 4.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                apply_misting_scenario(self.graph, DATASET, count, 10)
        with self.assertRaisesRegex(ValueError, "Cannot load"):
            apply_misting_scenario(self.graph, DATASET.parent / "tiny_campus_block", 4, 10)
        with self.assertRaisesRegex(ValueError, "anchor"):
            apply_misting_scenario(replace(self.graph, nodes={}), DATASET, 4, 10)
        with self.assertRaisesRegex(ValueError, "overlap"):
            apply_misting_scenario(self.graph, DATASET, 10, 100)

    def test_configuration_validation(self):
        config = json.loads((DATASET / "misting_scenarios.json").read_text())
        manifest = json.loads((DATASET / "dataset.json").read_text())
        for change, error in [
            ({"dataset_id": "another_map"}, "dataset_id"),
            ({"max_heat_reduction_f": -1}, "positive"),
            ({"placements": [{**config["placements"][0], "actuator_id": "SIM_S1"}]
              + config["placements"][1:]}, "duplicate"),
        ]:
            with self.subTest(error=error), patch.object(Path, "read_text", side_effect=[
                    json.dumps({**config, **change}), json.dumps(manifest)]):
                with self.assertRaisesRegex(ValueError, error):
                    apply_misting_scenario(self.graph, DATASET, 4, 10)

    def test_cli_default_and_run_metadata_saved(self):
        from main import parse_arguments, run_pipeline
        self.assertIsNone(parse_arguments([]).misting_count)
        for count in (0, 4, 6, 10):
            self.assertEqual(parse_arguments(["--misting-count", str(count)]).misting_count, count)
        graph, metadata = apply_misting_scenario(self.graph, DATASET, 4, 10)
        class Stage:
            def predict(self, model_input):
                return ModelOutput("test", None, (), {}, model_input.graph, {})
        with tempfile.TemporaryDirectory() as folder:
            output = run_pipeline(graph, [Stage()], folder, {"misting_scenario": metadata})
            saved = json.loads((Path(folder) / "01_test.json").read_text())
        self.assertEqual(output.diagnostics["misting_scenario"], metadata)
        self.assertEqual(saved["diagnostics"]["misting_scenario"], metadata)


if __name__ == "__main__":
    unittest.main()
