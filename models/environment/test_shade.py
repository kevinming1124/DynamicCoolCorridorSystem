"""USC shade integration and dataset-selection regression checks."""
import json
import unittest
from pathlib import Path

from models.environment.model import DEFAULT_DATASET_PATH, EnvironmentModel
from utils import ModelInput, load_campus_graph


ROOT = Path(__file__).resolve().parents[2]
USC = ROOT / "dataset" / "usc_osm_trimmed"


class TestGeneratedShade(unittest.TestCase):
    def test_usc_daily_shade_and_walkway_consistency(self):
        self.assertEqual(DEFAULT_DATASET_PATH, USC)
        manifest = json.loads((USC / "dataset.json").read_text())
        self.assertNotIn("shade_schedule", manifest["files"])
        graph = load_campus_graph(USC)
        model = EnvironmentModel.from_config()
        outputs = [model.predict(ModelInput(graph, context={
            "dataset_dir": str(USC), "time": time,
        })) for time in ("08:00", "12:00", "15:00")]
        groups = json.loads((USC / manifest["files"]["source_edges"]).read_text())["source_edges"]
        for output in outputs:
            shade = output.property_values["shade_pct"]
            self.assertEqual(len(shade), len(graph.edges))
            self.assertEqual(output.diagnostics["edges_with_auto_shade"], len(graph.edges))
            self.assertEqual(output.diagnostics["edges_with_scheduled_shade"], 0)
            self.assertTrue(all(0 <= value <= 100 for value in shade.values()))
            self.assertGreater(len(set(shade.values())), 1)
            for group in groups:
                self.assertEqual(len({shade[e] for e in group["pipeline_edges"]}), 1)
        morning, noon, afternoon = [o.property_values["shade_pct"] for o in outputs]
        self.assertTrue(any(morning[e] > noon[e] for e in morning))
        self.assertTrue(any(afternoon[e] > noon[e] for e in morning))

    def test_schedule_is_explicit_and_does_not_leak_between_datasets(self):
        model = EnvironmentModel.from_config()
        campus = ROOT / "dataset" / "campus_50"
        graph = load_campus_graph(campus)
        scheduled = model.predict(ModelInput(graph, context={"dataset_dir": str(campus)}))
        self.assertEqual(scheduled.diagnostics["edges_with_scheduled_shade"], len(graph.edges))
        automatic = model.predict(ModelInput(graph))
        self.assertEqual(automatic.diagnostics["edges_with_scheduled_shade"], 0)
        self.assertEqual(automatic.diagnostics["edges_with_auto_shade"], len(graph.edges))
        usc = load_campus_graph(USC)
        output = model.predict(ModelInput(usc, context={"dataset_dir": str(USC)}))
        self.assertEqual(output.diagnostics["edges_with_scheduled_shade"], 0)


if __name__ == "__main__":
    unittest.main()
