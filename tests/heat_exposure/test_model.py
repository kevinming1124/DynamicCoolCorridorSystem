"""USC integration and physical-response checks for heat exposure."""
import contextlib
import io
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from main import build_initialization_pipeline, run_pipeline
from models.heat_exposure.model import (
    DEFAULT_DATASET_PATH, HeatExposureModel, main, run_standalone,
)
from utils import load_campus_graph


USC = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


class TestHeatExposure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = run_standalone()

    def test_usc_outputs_match_pipeline(self):
        self.assertEqual(DEFAULT_DATASET_PATH, USC)
        output = self.output
        self.assertEqual(len(output.graph.edges), 930)
        self.assertEqual(output.diagnostics["edges_with_auto_shade"], 930)
        self.assertEqual(output.diagnostics["edges_with_scheduled_shade"], 0)
        pipeline = run_pipeline(
            load_campus_graph(USC), build_initialization_pipeline(USC),
            context={"dataset_dir": str(USC), "time": "13:00", "scenario": "hot"},
        )
        self.assertEqual(output.property_values, pipeline.property_values)
        for values in output.property_values.values():
            self.assertEqual(len(values), 930)
            self.assertTrue(all(math.isfinite(value) for value in values.values()))
        self.assertTrue(set(output.property_values["unsafe_heat"].values()) <= {0.0, 1.0})
        for name in ("heat_exposure_rate", "walking_heat_exposure"):
            self.assertTrue(all(value >= 0 for value in output.property_values[name].values()))

    def test_shade_reduces_radiant_temperature_and_heat(self):
        model = HeatExposureModel.from_config()
        graph = self.output.graph
        edge = graph.edges[0]
        exposed = model.calculate_edge(edge.with_property("shade_pct", 5), graph)
        shaded = model.calculate_edge(edge.with_property("shade_pct", 70), graph)
        self.assertLess(shaded["tmrt_c"], exposed["tmrt_c"])
        self.assertLess(shaded["utci_c"], exposed["utci_c"])
        self.assertLess(shaded["heat_exposure_rate"], exposed["heat_exposure_rate"])

    def test_walking_exposure_scales_with_duration(self):
        graph = self.output.graph
        edge = graph.edges[0]
        model = HeatExposureModel.from_config()
        baseline = model.calculate_edge(edge, graph)
        longer = model.calculate_edge(replace(edge, walk_time_min=edge.walk_time_min * 2), graph)
        self.assertGreater(baseline["walking_heat_exposure"], 0)
        self.assertEqual(longer["utci_c"], baseline["utci_c"])
        self.assertAlmostEqual(longer["walking_heat_exposure"],
                               baseline["walking_heat_exposure"] * 2, delta=0.015)

    def test_cli_saves_context_and_environment_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "heat.json"
            with contextlib.redirect_stdout(io.StringIO()):
                output = main(["--time", "15:00", "--scenario", "mild",
                               "--mister-on", "SIM_M1", "--save", str(destination)])
            saved = json.loads(destination.read_text())
        self.assertEqual(saved["diagnostics"]["time"], "15:00")
        self.assertEqual(saved["diagnostics"]["scenario"], "mild")
        self.assertEqual(saved["diagnostics"]["misters_on"], ["SIM_M1"])
        self.assertEqual(saved["diagnostics"]["edges_with_auto_shade"], 930)
        self.assertIn("shade_pct", output.graph.edges[0].properties)
        self.assertIn("air_temp_f", output.graph.edges[0].properties)

    def test_rejects_unknown_ids_and_signals_as_misters(self):
        for identifier in ("A01", "SIM_S1"):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "non-misting"):
                run_standalone(mister_ids=(identifier,))

    def test_other_dataset_remains_selectable(self):
        campus = USC.parent / "campus_50"
        output = run_standalone(campus, time="08:00", scenario="mild")
        self.assertEqual(len(output.graph.edges), 100)
        self.assertEqual(output.diagnostics["edges_with_scheduled_shade"], 100)


if __name__ == "__main__":
    unittest.main()
