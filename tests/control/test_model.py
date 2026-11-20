"""Radius boundaries and single-decision USC control checks."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from models.control.model import (
    ControlModel, ModelConfig, audit_control_rules, main, misting_command, run_standalone,
)
from models.environment import EnvironmentModel
from models.heat_exposure import HeatExposureModel
from utils import CampusGraph, GraphNode, GraphEdge, ModelInput, StaticActuator


def sample_graph(nearby_utci=36.0, status="off"):
    """Mister at the origin; only "safe" and "nearby" lie inside its 10 m zone."""
    coordinates = {"M": (0, 0), "A": (-1, 0), "B": (1, 0),
                   "C": (-20, 5), "D": (20, 5), "E": (-20, 10), "F": (20, 10),
                   "G": (-20, 11), "H": (20, 11), "I": (10, 0), "J": (20, 0)}
    nodes = {key: GraphNode(key, key, "intersection", x, y, True, "Test")
             for key, (x, y) in coordinates.items()}
    # Edges outside the zone are very hot, so reacting to them would be visible.
    records = [("safe", "A", "B", 30.0), ("nearby", "C", "D", nearby_utci),
               ("tangent", "E", "F", 45.0), ("outside", "G", "H", 45.0),
               ("boundary", "I", "J", 45.0), ("zero", "M", "M", 45.0)]

    def heat(utci):
        if isinstance(utci, (int, float)) and not isinstance(utci, bool):
            return {"uncooled_utci_c": utci, "unsafe_heat": 1.0 if utci >= 38 else 0.0}
        return {"uncooled_utci_c": utci, "unsafe_heat": 0.0}

    edges = tuple(GraphEdge(key, a, b, 40, 1, "concrete", True, properties=heat(utci))
                  for key, a, b, utci in records)
    mister = StaticActuator("M1", "misting_point", "M", 0, 0, properties={"status": status})
    return CampusGraph(nodes, edges, {"M1": mister})


class TestControlRules(unittest.TestCase):
    def setUp(self):
        self.model = ControlModel.from_config()

    def control(self, graph):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.model.predict(ModelInput(graph))

    def test_observes_crossing_segment_without_anchor_connection(self):
        graph = sample_graph()
        output = self.control(graph)
        self.assertEqual(output.graph.actuators["M1"].properties["status"], "on")
        report = audit_control_rules(graph, output.graph, 10)[0]
        self.assertEqual(set(report["observed_edges"]), {"safe", "nearby"})
        self.assertEqual(report["hottest_uncooled_utci_c"], 36.0)
        self.assertEqual(report["result"], "PASS")

    def test_tangency_boundary_and_zero_length_do_not_trigger_mister(self):
        graph = sample_graph(30.0)
        output = self.control(graph)
        self.assertEqual(output.graph.actuators["M1"].properties["status"], "off")
        self.assertEqual(audit_control_rules(graph, output.graph, 10)[0]["result"], "PASS")

    def test_dead_band_keeps_current_state(self):
        cases = [("off", 34.9, "off"), ("off", 35.0, "on"), ("on", 34.0, "on"),
                 ("on", 33.0, "on"), ("on", 32.9, "off"), ("off", 20.0, "off")]
        for previous, utci, expected in cases:
            with self.subTest(previous=previous, utci=utci):
                self.assertEqual(misting_command(previous, utci, 35.0, 33.0), expected)
                graph = sample_graph(utci, status=previous)
                output = self.control(graph)
                self.assertEqual(output.graph.actuators["M1"].properties["status"], expected)
                self.assertEqual(audit_control_rules(graph, output.graph, 10)[0]["result"], "PASS")

    def test_reads_uncooled_heat_not_cooled_heat(self):
        # A misted zone whose cooled UTCI looks safe must stay on while the
        # uncooled UTCI is still hot, so misting does not switch itself off.
        graph = sample_graph(37.0, status="on")
        graph = graph.with_edge_property("utci_c", {e.edge_id: 25.0 for e in graph.edges})
        output = self.control(graph)
        self.assertEqual(output.graph.actuators["M1"].properties["status"], "on")

    def test_threshold_configuration_is_validated(self):
        self.assertEqual((self.model.config.mist_on_utci_c, self.model.config.mist_off_utci_c),
                         (35.0, 33.0))
        base = {"model_name": "control_model", "description": "", "enabled": True}
        bad_parameters = [None, {"mist_on_utci_c": 35.0},
                          {"mist_on_utci_c": 33.0, "mist_off_utci_c": 35.0},
                          {"mist_on_utci_c": True, "mist_off_utci_c": 33.0},
                          {"mist_on_utci_c": float("nan"), "mist_off_utci_c": 33.0}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for parameters in bad_parameters:
                with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                    record = dict(base) if parameters is None else {**base, "parameters": parameters}
                    path.write_text(json.dumps(record))
                    ModelConfig.from_file(path)

    def test_empty_observation_and_invalid_heat_raise(self):
        graph = sample_graph()
        with self.assertRaisesRegex(ValueError, "no walkway segments"):
            self.control(replace(graph, edges=tuple(e for e in graph.edges if e.edge_id == "outside")))
        for value in (True, "36", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.control(sample_graph(value))

    def test_legacy_led_is_preserved_without_control(self):
        graph = sample_graph()
        led = StaticActuator("LED", "directional_led", "M", 0, 0,
                             properties={"status": "off", "recommended_edge": None})
        graph = replace(graph, actuators={**graph.actuators, "LED": led})
        output = self.control(graph)
        self.assertIs(output.graph.actuators["LED"], led)
        self.assertEqual(len(audit_control_rules(graph, output.graph, 10)), 1)

    def test_audit_detects_wrong_command(self):
        graph = sample_graph()
        self.assertEqual(audit_control_rules(graph, graph, 10)[0]["result"], "FAIL")

    def test_usc_layouts_and_hot_mild_rules(self):
        for count in (0, 4, 6, 10):
            for time, scenario in (("13:00", "hot"), ("08:00", "mild")):
                with self.subTest(count=count, scenario=scenario), contextlib.redirect_stdout(io.StringIO()):
                    output = run_standalone(time=time, scenario=scenario, misting_count=count)
                    self.assertEqual(output.diagnostics["misting_count"], count)
                    self.assertEqual(len(output.diagnostics["rule_checks"]), count + 2)
                    self.assertEqual(output.diagnostics["rule_check_status"], "PASS")
                    for report in output.diagnostics["rule_checks"]:
                        if scenario == "mild":
                            self.assertEqual(report["unsafe_segment_count"], 0)
                        self.assertEqual(report["actual"], report["expected"])

    def test_single_decision_save_and_no_effect_recalculation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            with patch.object(EnvironmentModel, "predict", autospec=True,
                              side_effect=EnvironmentModel.predict) as env_call:
                with patch.object(HeatExposureModel, "predict", autospec=True,
                                  side_effect=HeatExposureModel.predict) as heat_call:
                    with contextlib.redirect_stdout(io.StringIO()):
                        output = main(["--save", str(path)])
            saved = json.loads(path.read_text())
        self.assertEqual(env_call.call_count, 1)
        self.assertEqual(heat_call.call_count, 1)
        self.assertEqual(output.diagnostics["misting_count"], 4)
        self.assertEqual(saved["diagnostics"]["rule_check_status"], "PASS")
        for edge in output.graph.edges:
            self.assertEqual(edge.properties["mist_cooling_f"], 0)
            self.assertIn("unsafe_heat", edge.properties)

    def test_pipeline_cli_accepts_four_mister_layout(self):
        from main import main as pipeline_main

        printed = io.StringIO()
        with patch("sys.argv", ["main.py", "--misting-count", "4", "--rounds", "1"]):
            with contextlib.redirect_stdout(printed):
                pipeline_main()
        self.assertIn("Verification audit: PASS", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
