"""Per-walkway air-temperature offsets from OSM surface and surroundings."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from main import load_simulation_graph
from models.environment import EnvironmentModel
from models.verification.actuators import audit_actuators
from utils import CampusGraph, GraphEdge, GraphNode, ModelInput, load_campus_graph

USC = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


def record(surface="untagged", **distances):
    base = {"surface": surface, "tree_m": None, "green_m": None, "building_m": 5.0,
            "parking_m": None, "major_road_m": None, "fountain_m": None}
    return {**base, **distances}


class TestOffsetRules(unittest.TestCase):
    def setUp(self):
        self.model = EnvironmentModel.from_config()

    def test_surface_and_nearby_rules(self):
        offset = self.model.local_offset_for
        self.assertEqual(offset(record()), 0.0)
        self.assertEqual(offset(record("asphalt")), 1.5)
        self.assertEqual(offset(record("concrete")), 0.5)
        self.assertEqual(offset(record("unknown_tag")), 0.0)
        self.assertEqual(offset(record(tree_m=4.0)), -1.5)
        self.assertEqual(offset(record(tree_m=10.0)), -1.5)          # boundary counts
        self.assertEqual(offset(record(tree_m=10.01)), 0.0)
        self.assertEqual(offset(record(green_m=0.0, fountain_m=12.0)), -1.5)
        self.assertEqual(offset(record(parking_m=3.0, major_road_m=8.0)), 2.0)
        # Open plaza: no building within 20 m, or none recorded at all.
        self.assertEqual(offset(record(building_m=25.0)), 1.0)
        self.assertEqual(offset(record(building_m=None)), 1.0)

    def test_offsets_are_clamped(self):
        hot = record("asphalt", parking_m=1.0, major_road_m=1.0, building_m=None)
        cool = record(tree_m=1.0, green_m=1.0, fountain_m=1.0)
        self.assertEqual(self.model.local_offset_for(hot), 3.0)       # 4.5 before the clamp
        self.assertEqual(self.model.local_offset_for(cool), -3.0)

    def test_invalid_rules_are_rejected(self):
        model = EnvironmentModel.from_config()
        bad_rules = [
            {"nearby_rules": [{"feature": "tree_m", "offset_f": -1}]},
            {"nearby_rules": [{"feature": "tree_m", "within_m": 5, "beyond_m": 9, "offset_f": -1}]},
            {"surface_offsets_f": {"asphalt": "hot"}},
            {"min_offset_f": 2, "max_offset_f": 1},
        ]
        for rules in bad_rules:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                model.config.metadata["local_temperature"] = rules
                model._load_local_rules()


class TestUscOffsets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = EnvironmentModel.from_config()
        cls.graph, _, _ = load_simulation_graph(USC, 10)
        cls.output = cls.model.predict(ModelInput(cls.graph, context={
            "dataset_dir": str(USC), "time": "13:00", "scenario": "hot"}))

    def test_surroundings_file_matches_the_map(self):
        surroundings = json.loads((USC / "surroundings.json").read_text())
        edges = load_campus_graph(USC).edges
        self.assertEqual(set(surroundings["edges"]), {e.edge_id for e in edges})
        summary = surroundings["summary"]
        self.assertGreaterEqual(summary["matched_segments"] / summary["segments"], 0.95)
        self.assertLessEqual(summary["median_match_distance_m"], 1.0)

    def test_offsets_vary_and_feed_both_air_temperatures(self):
        values = self.output.property_values
        offsets = values["local_temp_offset_f"]
        self.assertGreater(len(set(offsets.values())), 5)
        self.assertTrue(all(-3.0 <= v <= 3.0 for v in offsets.values()))
        base = self.output.diagnostics["base_air_temp_f"]
        for edge_id, offset in offsets.items():
            self.assertEqual(values["uncooled_air_temp_f"][edge_id], round(base + offset, 2))
        self.assertEqual(self.output.diagnostics["local_offset_source"], "dataset surroundings file")

    def test_cut_pieces_share_their_walkway_offset(self):
        offsets = self.output.property_values["local_temp_offset_f"]
        pieces = [e for e in offsets if "~" in e]
        self.assertTrue(pieces)
        for edge_id in pieces:
            siblings = {offsets[e] for e in offsets if e.split("~")[0] == edge_id.split("~")[0]}
            self.assertEqual(len(siblings), 1, edge_id)

    def test_audit_checks_offset_consistency(self):
        snapshot = {"pedestrians": [], "simulation_time_sec": 0}
        good = audit_actuators(SimpleNamespace(graph=self.output.graph), self.output.diagnostics,
                               snapshot, snapshot, 0, 1e-6)
        self.assertEqual(good["misting_status"], "PASS", good["violations"][:3])
        edge = next(e for e in self.output.graph.edges if e.properties["local_temp_offset_f"] != 0)
        bad_graph = self.output.graph.with_edge_property("local_temp_offset_f", {
            e.edge_id: (0.0 if e is edge else e.properties["local_temp_offset_f"])
            for e in self.output.graph.edges})
        bad = audit_actuators(SimpleNamespace(graph=bad_graph), self.output.diagnostics,
                              snapshot, snapshot, 0, 1e-6)
        self.assertEqual(bad["misting_status"], "FAIL")

    def test_graph_without_surroundings_gets_no_offset(self):
        graph = CampusGraph(
            {"A": GraphNode("A", "", "intersection", 0, 0, True, ""),
             "B": GraphNode("B", "", "intersection", 50, 0, True, "")},
            (GraphEdge("E", "A", "B", 50, 0.6, "concrete", True),))
        output = self.model.predict(ModelInput(graph, context={"time": "13:00"}))
        self.assertEqual(output.property_values["local_temp_offset_f"], {"E": 0.0})
        self.assertTrue(output.diagnostics["local_offset_source"].startswith("none"))


if __name__ == "__main__":
    unittest.main()
