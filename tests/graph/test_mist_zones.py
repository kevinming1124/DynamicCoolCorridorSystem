"""Cutting walkways at misting circles."""
import math
import unittest
from pathlib import Path

from main import load_simulation_graph
from models.environment.model import _segment_circle_coverage_ratio
from utils import CampusGraph, GraphEdge, GraphNode, StaticActuator
from utils.graph.mist_zones import expand_split_ids, parent_edge_id, split_edges_at_mist_zones

USC = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


def node(name, x, y):
    return GraphNode(name, name, "intersection", x, y, True, "Test node")


def sample_graph():
    """Mister at the origin, radius 10 m, and one signal anchored at A."""
    nodes = {key: node(key, x, y) for key, (x, y) in {
        "A": (-30, 0), "B": (30, 0),      # crosses the circle: cut twice
        "C": (5, -30), "D": (5, 0),       # ends inside the circle: cut once
        "E": (-3, 3), "F": (3, 3),        # fully inside: unchanged
        "G": (-20, 10), "H": (20, 10),    # tangent: unchanged
        "I": (-20, 40), "J": (20, 40),    # outside: unchanged
    }.items()}
    edges = (GraphEdge("cross", "A", "B", 66, 0.8, "concrete", True, properties={"x": 1.0}),
             GraphEdge("end_in", "C", "D", 30, 0.4, "concrete", True),
             GraphEdge("inside", "E", "F", 6, 0.1, "concrete", True),
             GraphEdge("tangent", "G", "H", 40, 0.5, "concrete", True),
             GraphEdge("outside", "I", "J", 40, 0.5, "concrete", True))
    actuators = {
        "M": StaticActuator("M", "misting_point", "E", 0, 0, max_heat_reduction_f=10.8,
                            properties={"status": "off"}),
        "S": StaticActuator("S", "traffic_signal", "A", -30, 0, controlled_edges=("cross", "outside"),
                            default_wait_time_s=30, min_wait_time_s=10, max_wait_time_s=90,
                            properties={"status": "on", "wait_time_s": 30}),
    }
    return CampusGraph(nodes, edges, actuators)


class TestSplitAtMistZones(unittest.TestCase):
    def test_pieces_follow_the_circle_and_keep_totals(self):
        graph, summary = split_edges_at_mist_zones(sample_graph(), 10)
        edges = {edge.edge_id: edge for edge in graph.edges}
        self.assertEqual(sorted(edges), ["cross~1", "cross~2", "cross~3", "end_in~1", "end_in~2",
                                         "inside", "outside", "tangent"])
        self.assertEqual(summary["split_walkways"], 2)
        self.assertEqual(summary["added_nodes"], 3)
        # Walking order and connectivity from the original from_node.
        chain = [edges[f"cross~{k}"] for k in (1, 2, 3)]
        self.assertEqual([chain[0].from_node, chain[-1].to_node], ["A", "B"])
        self.assertTrue(all(a.to_node == b.from_node for a, b in zip(chain, chain[1:])))
        # Distances and walking time split in proportion: 20 m of 60 m is inside.
        self.assertAlmostEqual(sum(e.distance_m for e in chain), 66)
        self.assertAlmostEqual(chain[1].distance_m, 66 / 3)
        self.assertAlmostEqual(sum(e.walk_time_min for e in chain), 0.8)
        self.assertEqual(chain[1].properties, {"x": 1.0})
        # Boundary nodes sit on the circle; each piece is fully inside or outside.
        for identifier in ("cross~n1", "cross~n2", "end_in~n1"):
            boundary = graph.nodes[identifier]
            self.assertAlmostEqual(math.hypot(boundary.x_m, boundary.y_m), 10)
        for edge in graph.edges:
            a, b = graph.nodes[edge.from_node], graph.nodes[edge.to_node]
            share = _segment_circle_coverage_ratio(0, 0, 10, a.x_m, a.y_m, b.x_m, b.y_m)
            with self.subTest(edge=edge.edge_id):
                self.assertTrue(share == 0 or math.isclose(share, 1), share)

    def test_signal_references_follow_the_pieces(self):
        graph, _ = split_edges_at_mist_zones(sample_graph(), 10)
        self.assertEqual(graph.actuators["S"].controlled_edges,
                         ("cross~1", "cross~2", "cross~3", "outside"))

    def test_helpers_and_invalid_inputs(self):
        graph, _ = split_edges_at_mist_zones(sample_graph(), 10)
        self.assertEqual(parent_edge_id("cross~2"), "cross")
        self.assertEqual(parent_edge_id("outside"), "outside")
        self.assertEqual(expand_split_ids(["end_in", "inside"], graph), ["end_in~1", "end_in~2", "inside"])
        for radius in (0, -1, float("nan"), True):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                split_edges_at_mist_zones(sample_graph(), radius)
        with self.assertRaisesRegex(ValueError, "already been split"):
            split_edges_at_mist_zones(graph, 10)

    def test_graph_without_misters_is_unchanged(self):
        base = sample_graph()
        graph = CampusGraph(base.nodes, base.edges, {"S": base.actuators["S"]})
        split, summary = split_edges_at_mist_zones(graph, 10)
        self.assertEqual(split.edges, graph.edges)
        self.assertEqual(summary["split_walkways"], 0)

    def test_usc_layouts_cut_more_walkways_with_more_misters(self):
        counts = {}
        for misting_count in (None, 0, 4, 10):
            graph, _, summary = load_simulation_graph(USC, misting_count)
            counts[misting_count] = summary["split_walkways"]
            self.assertEqual(len(graph.edges), 930 + summary["added_nodes"])
        self.assertEqual(counts[0], 0)
        self.assertLess(counts[None], counts[4])
        self.assertLess(counts[4], counts[10])


if __name__ == "__main__":
    unittest.main()
