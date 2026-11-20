"""Analytic coverage examples and end-to-end edge cooling checks."""
import unittest
from types import SimpleNamespace

from models.environment.model import EnvironmentModel, _segment_circle_coverage_ratio
from models.verification.actuators import audit_actuators, segment_circle_coverage
from utils import CampusGraph, GraphEdge, GraphNode, ModelInput, StaticActuator
from utils.graph.mist_zones import split_edges_at_mist_zones


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

    def _line_graph(self, status="on"):
        return CampusGraph(
            nodes={"A": node("A", -31.25, 0), "B": node("B", 31.25, 0)},
            # 32% of the edge is inside the zone; stored route length
            # deliberately differs from coordinate length.
            edges=(GraphEdge("E", "A", "B", 100, 1, "concrete", True),),
            actuators={"M": StaticActuator(
                "M", "misting_point", "A", 0, 0,
                max_heat_reduction_f=10.8, properties={"status": status})})

    def audit(self, result, graph=None):
        snapshot = {"pedestrians": [], "simulation_time_sec": 0}
        return audit_actuators(SimpleNamespace(graph=graph or result.graph), result.diagnostics,
                               snapshot, snapshot, 0, 1e-6)

    def test_uncut_edge_gets_covered_share_and_passes_audit(self):
        model = EnvironmentModel.from_config()
        self.assertEqual(model.config.parameters["mist_range_m"], 10)
        result = model.predict(ModelInput(self._line_graph()))
        props = result.graph.edges[0].properties
        base_temp = result.diagnostics["base_air_temp_f"]
        base_humidity = result.diagnostics["base_humidity_pct"]
        self.assertEqual(props["mist_cooling_f"], round(10.8 * .32, 2))
        self.assertEqual(props["air_temp_f"], round(base_temp - 10.8 * .32, 2))
        self.assertEqual(props["humidity_pct"], round(base_humidity + 5 * .32, 2))
        self.assertEqual(props["uncooled_air_temp_f"], round(base_temp, 2))
        self.assertEqual(props["uncooled_humidity_pct"], round(base_humidity, 2))
        self.assertEqual(self.audit(result)["misting_status"], "PASS", self.audit(result)["violations"])
        for name, value in (("mist_cooling_f", 10.8), ("uncooled_air_temp_f", base_temp - 3.46)):
            with self.subTest(corrupted=name):
                bad_graph = result.graph.with_edge_property(name, {"E": value})
                self.assertEqual(self.audit(result, bad_graph)["misting_status"], "FAIL")
        off = model.predict(ModelInput(self._line_graph("off")))
        self.assertEqual(off.property_values["mist_cooling_f"]["E"], 0)
        self.assertEqual(off.property_values["humidity_pct"]["E"], round(off.diagnostics["base_humidity_pct"], 2))
        self.assertEqual(off.property_values["air_temp_f"]["E"],
                         off.property_values["uncooled_air_temp_f"]["E"])

    def test_cut_walkway_cools_only_the_piece_inside_the_circle(self):
        model = EnvironmentModel.from_config()
        graph, summary = split_edges_at_mist_zones(self._line_graph(), 10)
        self.assertEqual(summary["split_walkways"], 1)
        result = model.predict(ModelInput(graph))
        cooling = result.property_values["mist_cooling_f"]
        self.assertEqual(cooling, {"E~1": 0.0, "E~2": 10.8, "E~3": 0.0})
        humidity = result.property_values["humidity_pct"]
        base_humidity = result.diagnostics["base_humidity_pct"]
        self.assertEqual(humidity["E~2"], round(base_humidity + 5, 2))
        self.assertEqual(humidity["E~1"], round(base_humidity, 2))
        self.assertEqual(self.audit(result)["misting_status"], "PASS", self.audit(result)["violations"])

    def test_strongest_mister_counts_without_stacking(self):
        model = EnvironmentModel.from_config()
        # Strong mister S at (0, 0), weak mister W at (-20, 5); both radius 10 m.
        nodes = {name: node(name, x, y) for name, (x, y) in {
            "A": (-30, 0), "B": (30, 0),      # y=0: partly inside both circles
            "C": (-30, 12), "D": (-15, 12),   # 7 m from W, 19 m from S
            "E": (-10, 10), "F": (10, 10),    # tangent to S, 11.2 m from W
        }.items()}
        edges = (GraphEdge("both", "A", "B", 60, 1, "concrete", True),
                 GraphEdge("weak", "C", "D", 15, 1, "concrete", True),
                 GraphEdge("tangent", "E", "F", 20, 1, "concrete", True))
        misters = {
            "S": StaticActuator("S", "misting_point", "A", 0, 0,
                                max_heat_reduction_f=10.8, properties={"status": "on"}),
            "W": StaticActuator("W", "misting_point", "C", -20, 5,
                                max_heat_reduction_f=5.4, properties={"status": "on"}),
        }
        result = model.predict(ModelInput(CampusGraph(nodes, edges, misters)))
        cooling = result.property_values["mist_cooling_f"]
        strong_share = _segment_circle_coverage_ratio(0, 0, 10, -30, 0, 30, 0)
        weak_share = _segment_circle_coverage_ratio(-20, 5, 10, -30, 0, 30, 0)
        self.assertAlmostEqual(strong_share, 1 / 3)
        self.assertEqual(cooling["both"], round(10.8 * strong_share, 2))   # not the sum
        self.assertLess(cooling["both"], round(10.8 * strong_share + 5.4 * weak_share, 2))
        self.assertEqual(cooling["weak"],
                         round(5.4 * _segment_circle_coverage_ratio(-20, 5, 10, -30, 12, -15, 12), 2))
        self.assertEqual(cooling["tangent"], 0)
        self.assertEqual(self.audit(result)["misting_status"], "PASS", self.audit(result)["violations"])

    def test_boundary_slivers_count_as_outside(self):
        # A piece that starts exactly on the circle must not pick up rounding-error cooling.
        self.assertEqual(_segment_circle_coverage_ratio(0, 0, 10, 10, 0, 20, 0), 0.0)
        self.assertEqual(_segment_circle_coverage_ratio(0, 0, 10, 10 - 1e-13, 0, 20, 0), 0.0)
        self.assertEqual(segment_circle_coverage(0, 0, 10, node("A", 10 - 1e-13, 0), node("B", 20, 0)), 0.0)


if __name__ == "__main__":
    unittest.main()
