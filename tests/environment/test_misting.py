"""Analytic coverage examples and end-to-end edge cooling checks."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from models.environment.model import EnvironmentModel, _segment_circle_coverage_ratio
from models.verification.actuators import audit_actuators, segment_circle_coverage
from utils import CampusGraph, GraphEdge, GraphNode, ModelInput, StaticActuator


def node(name, x, y):
    return GraphNode(name, name, "intersection", x, y, True, "Test node")


class TestMistingCoverage(unittest.TestCase):
    def test_analytic_intersections(self):
        cases = [
            ("32 percent", (-31.25, 0, 31.25, 0), 10, 0.32),
            ("fully inside", (-3, 4, 3, 4), 10, 1.0),
            ("outside", (11, 0, 20, 0), 10, 0.0),
            ("tangent", (-20, 10, 20, 10), 10, 0.0),
            ("endpoint touch", (10, 0, 20, 0), 10, 0.0),
            ("one endpoint inside", (0, 0, 20, 0), 10, 0.5),
            ("offset chord", (-20, 6, 20, 6), 10, 0.4),
            ("vertical", (0, -20, 0, 20), 10, 0.5),
            ("zero length", (0, 0, 0, 0), 10, 0.0),
            ("zero radius", (-20, 0, 20, 0), 0, 0.0),
        ]
        for name, (ax, ay, bx, by), radius, expected in cases:
            with self.subTest(name=name):
                for a, b in [((ax, ay), (bx, by)), ((bx, by), (ax, ay))]:
                    self.assertAlmostEqual(_segment_circle_coverage_ratio(
                        0, 0, radius, *a, *b), expected)
                    self.assertAlmostEqual(segment_circle_coverage(
                        0, 0, radius, node("A", *a), node("B", *b)), expected)

    def test_coverage_scales_temperature_humidity_and_passes_audit(self):
        model = EnvironmentModel.from_config()
        self.assertEqual(model.config.parameters["mist_range_m"], 10)
        graph = CampusGraph(
            nodes={"A": node("A", -31.25, 0), "B": node("B", 31.25, 0)},
            # Stored route length deliberately differs from coordinate length.
            edges=(GraphEdge("E", "A", "B", 100, 1, "concrete", True),),
            actuators={"M": StaticActuator(
                "M", "misting_point", "A", 0, 0,
                max_heat_reduction_f=5.4, properties={"status": "on"})})
        result = model.predict(ModelInput(graph))
        props = result.graph.edges[0].properties
        self.assertEqual(props["mist_cooling_f"], 1.73)
        self.assertEqual(props["air_temp_f"],
                         round(result.diagnostics["base_air_temp_f"] - 5.4 * .32, 2))
        self.assertEqual(props["humidity_pct"],
                         round(result.diagnostics["base_humidity_pct"] + 5 * .32, 2))
        snapshot = {"pedestrians": [], "simulation_time_sec": 0}
        audit = audit_actuators(SimpleNamespace(graph=result.graph), result.diagnostics,
                                snapshot, snapshot, 0, 1e-6)
        self.assertEqual(audit["misting_status"], "PASS", audit["violations"])
        bad_graph = result.graph.with_edge_property("mist_cooling_f", {"E": 5.4})
        bad_audit = audit_actuators(SimpleNamespace(graph=bad_graph), result.diagnostics,
                                    snapshot, snapshot, 0, 1e-6)
        self.assertEqual(bad_audit["misting_status"], "FAIL")
        off_graph = replace(graph, actuators={"M": graph.actuators["M"].with_property("status", "off")})
        off = model.predict(ModelInput(off_graph))
        self.assertEqual(off.property_values["mist_cooling_f"]["E"], 0)
        self.assertEqual(off.property_values["humidity_pct"]["E"],
                         round(off.diagnostics["base_humidity_pct"], 2))


if __name__ == "__main__":
    unittest.main()
