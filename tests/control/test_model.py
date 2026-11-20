"""Radius boundaries and single-decision USC control checks."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from models.control.model import ControlModel, audit_control_rules, main, run_standalone
from models.environment import EnvironmentModel
from models.heat_exposure import HeatExposureModel
from utils import CampusGraph, GraphNode, GraphEdge, ModelInput, StaticActuator


def sample_graph(nearby_unsafe=1.0):
    coordinates = {"M": (0, 0), "A": (-1, 0), "B": (1, 0),
                   "C": (-20, 5), "D": (20, 5), "E": (-20, 10), "F": (20, 10),
                   "G": (-20, 11), "H": (20, 11), "I": (10, 0), "J": (20, 0)}
    nodes = {key: GraphNode(key, key, "intersection", x, y, True, "Test")
             for key, (x, y) in coordinates.items()}
    records = [("safe", "A", "B", 0.0), ("nearby", "C", "D", nearby_unsafe),
               ("tangent", "E", "F", 1.0), ("outside", "G", "H", 1.0),
               ("boundary", "I", "J", 1.0), ("zero", "M", "M", 1.0)]
    edges = tuple(GraphEdge(key, a, b, 40, 1, "concrete", True,
                            properties={"unsafe_heat": unsafe}) for key, a, b, unsafe in records)
    mister = StaticActuator("M1", "misting_point", "M", 0, 0, properties={"status": "off"})
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
        self.assertEqual(report["unsafe_segment_count"], 1)
        self.assertEqual(report["result"], "PASS")

    def test_tangency_boundary_and_zero_length_do_not_trigger_mister(self):
        graph = sample_graph(0.0)
        output = self.control(graph)
        self.assertEqual(output.graph.actuators["M1"].properties["status"], "off")
        self.assertEqual(audit_control_rules(graph, output.graph, 10)[0]["result"], "PASS")

    def test_empty_observation_and_invalid_flags_raise(self):
        graph = sample_graph()
        with self.assertRaisesRegex(ValueError, "no walkway segments"):
            self.control(replace(graph, edges=tuple(e for e in graph.edges if e.edge_id == "outside")))
        for value in (2.0, True, "1"):
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
