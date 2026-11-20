"""Safe-first routing, coolest fallback, time bound and mid-trip replanning."""
import contextlib
import io
import unittest

from models.pedestrian.agent import PedestrianState
from models.pedestrian.model import PedestrianSimulator
from models.routing.model import RoutingModel, TripRequest
from models.routing.policy import PolicyRouter
from utils import CampusGraph, GraphEdge, GraphNode, ModelInput, StaticActuator

SPEED = 1.0


def node(name, x, y):
    return GraphNode(name, "", "intersection", x, y, True, "")


def heat(rate, unsafe):
    return {"heat_exposure_rate": float(rate), "unsafe_heat": float(unsafe),
            "utci_c": 26.0 + rate, "blocked": 0.0}


def diamond(short_rate=14.0, short_unsafe=1.0, long_rate=10.0, long_unsafe=0.0, long_length=150.0,
            signal_wait=None):
    """A -> D by a short top path (via B, 100 m) or a longer bottom path (via C)."""
    nodes = {"A": node("A", 0, 0), "B": node("B", 50, 10), "C": node("C", 50, -40), "D": node("D", 100, 0)}
    half = long_length / 2
    edges = (GraphEdge("AB", "A", "B", 50, 50 / 60, "c", True, heat(short_rate, short_unsafe)),
             GraphEdge("BD", "B", "D", 50, 50 / 60, "c", True, heat(short_rate, short_unsafe)),
             GraphEdge("AC", "A", "C", half, half / 60, "c", True, heat(long_rate, long_unsafe)),
             GraphEdge("CD", "C", "D", half, half / 60, "c", True, heat(long_rate, long_unsafe)))
    actuators = {}
    if signal_wait is not None:
        actuators["S"] = StaticActuator("S", "traffic_signal", "A", 0, 0, controlled_edges=("AC",),
                                        default_wait_time_s=30, min_wait_time_s=0, max_wait_time_s=600,
                                        properties={"status": "on", "wait_time_s": signal_wait})
    return CampusGraph(nodes, edges, actuators)


def plan(graph, preference=0.0, ratio=2.5, **kwargs):
    walkability = {e.edge_id: 1 - min(1, e.properties["heat_exposure_rate"] / 20) for e in graph.edges}
    router = PolicyRouter(graph, {e.edge_id: 0.0 for e in graph.edges}, ratio)
    return router.plan("A", ["D"], SPEED, preference, walkability, **kwargs)


def edge_ids(result):
    return [step[2] for step in result.steps]


class TestPolicyRouter(unittest.TestCase):
    def test_safe_route_wins_over_shorter_unsafe_route_for_any_preference(self):
        for preference in (0.0, 0.5, 1.0):
            with self.subTest(preference=preference):
                result = plan(diamond(), preference)
                self.assertEqual(result.policy, "safe_route")
                self.assertEqual(edge_ids(result), ["AC", "CD"])
                self.assertTrue(result.within_time_bound)

    def test_coolest_fallback_minimizes_total_exposure_not_distance(self):
        # Both paths unsafe: short path 100 m at rate 14 = 23.3 deg-min;
        # long path 150 m at rate 8 = 20 deg-min, so the long path is coolest.
        result = plan(diamond(long_rate=8.0, long_unsafe=1.0))
        self.assertEqual(result.policy, "coolest_fallback")
        self.assertEqual(edge_ids(result), ["AC", "CD"])
        self.assertAlmostEqual(result.cumulative_exposure_degree_min, 8 * 150 / 60)
        self.assertEqual(result.maximum_utci_c, 34.0)
        # Make the long path hotter overall and the short one wins.
        self.assertEqual(edge_ids(plan(diamond(long_rate=12.0, long_unsafe=1.0))), ["AB", "BD"])

    def test_signal_waits_count_toward_exposure_and_time(self):
        # A 60 s wait at rate 8 adds 8 deg-min, making the long path hotter (28 > 23.3).
        result = plan(diamond(long_rate=8.0, long_unsafe=1.0, signal_wait=60))
        self.assertEqual(edge_ids(result), ["AB", "BD"])
        safe = plan(diamond(signal_wait=60))
        self.assertEqual(safe.waiting_time_s, 60)
        self.assertEqual(safe.travel_time_s, 150 + 60)

    def test_time_bound_falls_back_to_fastest_safe_route(self):
        # Safe path is 300 m; fastest path is 100 m; bound 2.5x = 250 s at 1 m/s.
        result = plan(diamond(long_length=300.0), preference=1.0)
        self.assertEqual(result.policy, "safe_route")
        self.assertFalse(result.within_time_bound)        # safety takes precedence
        self.assertIn("exceeds the time bound", result.note)
        self.assertEqual(plan(diamond(long_length=200.0), ratio=2.5).within_time_bound, True)

    def test_force_fallback_and_unreachable(self):
        self.assertEqual(plan(diamond(), force_fallback=True).policy, "coolest_fallback")
        graph = diamond()
        router = PolicyRouter(graph, {"AB": 1.0, "BD": 1.0, "AC": 1.0, "CD": 1.0}, 2.5)
        self.assertIsNone(router.plan("A", ["D"], SPEED, 0.0, {e.edge_id: 1.0 for e in graph.edges}))
        with self.assertRaises(ValueError):
            PolicyRouter(graph, {}, 0.5)


class TestReplanning(unittest.TestCase):
    def test_replace_remaining_route_keeps_progress_and_committed_edge(self):
        graph = diamond()
        simulator = PedestrianSimulator(graph)
        simulator.assign_route(1, ["A", "B", "D"], walking_speed_mps=SPEED)
        self.assertEqual(simulator.replanning_node_index(1), 0)
        simulator.step(10)                                  # 10 m along A->B: committed
        self.assertEqual(simulator.replanning_node_index(1), 1)
        with self.assertRaises(ValueError):
            simulator.replace_remaining_route(1, ["A", "C", "D"])   # must start at B
        simulator.replace_remaining_route(1, ["B", "D"])
        agent = simulator.get_pedestrian(1)
        self.assertEqual((agent.route, agent.position_m, agent.total_travel_time_sec), (["A", "B", "D"], 10, 10))

    def test_uncommitted_pedestrian_can_change_its_next_edge(self):
        simulator = PedestrianSimulator(diamond())
        simulator.assign_route(1, ["A", "B", "D"], walking_speed_mps=SPEED)
        simulator.replace_remaining_route(1, ["A", "C", "D"])
        self.assertEqual(simulator.get_pedestrian(1).route, ["A", "C", "D"])
        simulator.step(1000)
        self.assertEqual(simulator.get_pedestrian(1).state, PedestrianState.ARRIVED)
        with self.assertRaises(ValueError):
            simulator.replace_remaining_route(1, ["D"])

    def test_routing_stage_replans_when_heat_changes_mid_trip(self):
        config = {"model_name": "routingc_model", "enabled": True, "property_target": "edge",
                  "output_properties": ["walkability", "blocked", "planned_pedestrians"],
                  "parameters": {"discomfort_scale_c": 20.0, "maximum_arrival_ratio": 2.5}}
        # Start: everything safe, so the short top path is chosen.
        graph = diamond(short_unsafe=0.0, short_rate=5.0)
        simulator = PedestrianSimulator(graph)
        routing = RoutingModel(config, simulator, requests=[TripRequest(1, "A", "D", 0.0, SPEED)])
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet:
            first = routing.predict(ModelInput(graph))
        self.assertEqual(first.diagnostics["routes"][0]["edges"], ["AB", "BD"])
        self.assertEqual(first.diagnostics["routes"][0]["route_policy"], "safe_route")
        # Still at A: the top path turns unsafe, so the next round reroutes via C.
        hot = diamond()
        with contextlib.redirect_stdout(io.StringIO()):
            second = routing.predict(ModelInput(hot))
        changes = second.diagnostics["replanned_routes"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["new_tail"], ["A", "C", "D"])
        self.assertEqual(simulator.get_pedestrian(1).route, ["A", "C", "D"])
        # Same heat again: nothing changes.
        with contextlib.redirect_stdout(io.StringIO()):
            third = routing.predict(ModelInput(hot))
        self.assertEqual(third.diagnostics["replanned_routes"], [])
        self.assertEqual(third.diagnostics["replan_count_total"], 1)


if __name__ == "__main__":
    unittest.main()
