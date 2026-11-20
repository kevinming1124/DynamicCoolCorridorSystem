"""
Unit tests for pedestrian movement.

Verifies pedestrian movement along campus graph edges,
transitions between edges, destination arrival, and
validation of invalid movement conditions.
"""

import unittest
from dataclasses import replace

from models.pedestrian.agent import Pedestrian, PedestrianState
from models.pedestrian.movement import (
    get_edge_distance,
    move_pedestrian,
)

from utils.graph.graph import CampusGraph
from utils.graph.entities import GraphEdge, GraphNode, StaticActuator


class TestPedestrianMovement(unittest.TestCase):

    def setUp(self):
        """
        Create a small campus graph before each test.

        A -------- B ----- C
             100m      50m
        """

        self.graph = CampusGraph(
            nodes={
                node_id: GraphNode(
                    node_id=node_id,
                    location_name=f"Location {node_id}",
                    location_type="intersection",
                    x_m=x,
                    y_m=0.0,
                    accessible=True,
                    description="Test location",
                )
                for node_id, x in [
                    ("A", 0.0),
                    ("B", 100.0),
                    ("C", 150.0),
                ]
            },
            edges=(
                GraphEdge(
                    edge_id="E1",
                    from_node="A",
                    to_node="B",
                    distance_m=100.0,
                    walk_time_min=100.0 / 1.4 / 60.0,
                    surface_type="concrete",
                    accessible=True,
                ),
                GraphEdge(
                    edge_id="E2",
                    from_node="B",
                    to_node="C",
                    distance_m=50.0,
                    walk_time_min=50.0 / 1.4 / 60.0,
                    surface_type="concrete",
                    accessible=True,
                ),
            ),
        )

        self.pedestrian = Pedestrian(
            pedestrian_id=1,
            origin="A",
            destination="C",
            route=["A", "B", "C"],
            walking_speed_mps=1.4,
        )

    def test_single_edge_movement(self):
        """A pedestrian moves 1.4 meters in one second."""

        move_pedestrian(self.pedestrian, self.graph, dt=1.0)

        self.assertAlmostEqual(
            self.pedestrian.position_m,
            1.4,
        )

        self.assertEqual(
            self.pedestrian.current_edge_index,
            0,
        )

        self.assertEqual(
            self.pedestrian.state,
            PedestrianState.WALKING,
        )

        self.assertAlmostEqual(
            self.pedestrian.total_travel_time_sec,
            1.0,
        )

    def test_edge_transition(self):
        """
        A pedestrian carries unused movement distance
        into the next edge.
        """

        self.pedestrian.position_m = 99.0

        move_pedestrian(self.pedestrian, self.graph, dt=1.0)

        self.assertEqual(
            self.pedestrian.current_edge_index,
            1,
        )

        self.assertAlmostEqual(
            self.pedestrian.position_m,
            0.4,
        )

        self.assertEqual(
            self.pedestrian.state,
            PedestrianState.WALKING,
        )

    def test_destination_arrival(self):
        """A pedestrian is marked ARRIVED at the destination."""

        self.pedestrian.current_edge_index = 1
        self.pedestrian.position_m = 49.0

        move_pedestrian(self.pedestrian, self.graph, dt=1.0)

        self.assertEqual(
            self.pedestrian.state,
            PedestrianState.ARRIVED,
        )

        self.assertEqual(
            self.pedestrian.current_edge_index,
            2,
        )

        self.assertAlmostEqual(
            self.pedestrian.position_m,
            0.0,
        )

        # Only 1 meter remains at 1.4 m/s.
        self.assertAlmostEqual(
            self.pedestrian.total_travel_time_sec,
            1.0 / 1.4,
        )

    def test_multiple_edge_traversal(self):
        """A large timestep can traverse multiple edges."""

        move_pedestrian(self.pedestrian, self.graph, dt=120.0)

        self.assertEqual(
            self.pedestrian.state,
            PedestrianState.ARRIVED,
        )

        self.assertEqual(
            self.pedestrian.current_edge_index,
            2,
        )

        self.assertAlmostEqual(
            self.pedestrian.total_travel_time_sec,
            150.0 / 1.4,
        )

    def test_waiting_pedestrian_does_not_move(self):
        """Waiting pedestrians retain their position."""

        self.pedestrian.state = PedestrianState.WAITING
        self.pedestrian.position_m = 25.0

        move_pedestrian(self.pedestrian, self.graph, dt=1.0)

        self.assertAlmostEqual(
            self.pedestrian.position_m,
            25.0,
        )

        self.assertAlmostEqual(
            self.pedestrian.total_travel_time_sec,
            0.0,
        )

    def test_arrived_pedestrian_does_not_move(self):
        """Arrived pedestrians remain at their destination."""

        self.pedestrian.state = PedestrianState.ARRIVED
        self.pedestrian.current_edge_index = 2

        move_pedestrian(self.pedestrian, self.graph, dt=1.0)

        self.assertEqual(
            self.pedestrian.current_edge_index,
            2,
        )

        self.assertEqual(
            self.pedestrian.state,
            PedestrianState.ARRIVED,
        )

    def test_negative_timestep(self):
        """Negative timesteps are rejected."""

        with self.assertRaises(ValueError):
            move_pedestrian(
                self.pedestrian,
                self.graph,
                dt=-1.0,
            )

    def test_invalid_route(self):
        """A route containing nonexistent edges is rejected."""

        self.pedestrian.route = ["A", "C"]

        with self.assertRaises(ValueError):
            move_pedestrian(
                self.pedestrian,
                self.graph,
                dt=1.0,
            )

    def test_invalid_route_does_not_change_position(self):
        """Route validation must happen before movement."""

        self.pedestrian.route = ["A", "B", "D"]

        original_position = self.pedestrian.position_m
        original_index = self.pedestrian.current_edge_index

        with self.assertRaises(ValueError):
            move_pedestrian(
                self.pedestrian,
                self.graph,
                dt=1.0,
            )

        self.assertEqual(
            self.pedestrian.position_m,
            original_position,
        )

        self.assertEqual(
            self.pedestrian.current_edge_index,
            original_index,
        )

        self.assertAlmostEqual(
            self.pedestrian.total_travel_time_sec,
            0.0,
        )

    def test_inaccessible_edge(self):
        """Pedestrians cannot traverse inaccessible edges."""

        inaccessible_graph = CampusGraph(
            nodes=self.graph.nodes,
            edges=(
                GraphEdge(
                    edge_id="E1",
                    from_node="A",
                    to_node="B",
                    distance_m=100.0,
                    walk_time_min=1.0,
                    surface_type="concrete",
                    accessible=False,
                ),
            ),
        )

        pedestrian = Pedestrian(
            pedestrian_id=2,
            origin="A",
            destination="B",
            route=["A", "B"],
        )

        with self.assertRaises(ValueError):
            move_pedestrian(
                pedestrian,
                inaccessible_graph,
                dt=1.0,
            )

    def test_bidirectional_edge_lookup(self):
        """Edges can be traversed in either direction."""

        distance = get_edge_distance(
            self.graph,
            from_node="B",
            to_node="A",
        )

        self.assertAlmostEqual(distance, 100.0)

    def test_zero_timestep(self):
        """A zero-second update does not move the pedestrian."""

        move_pedestrian(self.pedestrian, self.graph, dt=0.0)

        self.assertAlmostEqual(self.pedestrian.position_m, 0.0)
        self.assertAlmostEqual(self.pedestrian.total_travel_time_sec, 0.0)

    def test_non_finite_timestep(self):
        """NaN and infinite timesteps must be rejected."""

        invalid_timesteps = [
            float("nan"),
            float("inf"),
            float("-inf"),
        ]

        for dt in invalid_timesteps:
            with self.subTest(dt=dt):
                with self.assertRaises(ValueError):
                    move_pedestrian(
                        self.pedestrian,
                        self.graph,
                        dt=dt,
                    )

    def test_invalid_pedestrian_position(self):
        """Positions outside the current edge must be rejected."""

        invalid_positions = [
            -1.0,
            101.0,
            float("nan"),
            float("inf"),
        ]

        for position in invalid_positions:
            with self.subTest(position=position):

                self.pedestrian.position_m = position

                with self.assertRaises(ValueError):
                    move_pedestrian(
                        self.pedestrian,
                        self.graph,
                        dt=1.0,
                    )

    def test_invalid_walking_speed(self):
        """Walking speeds must be finite and positive."""

        invalid_speeds = [
            0.0,
            -1.0,
            float("nan"),
            float("inf"),
            float("-inf"),
        ]

        for speed in invalid_speeds:
            with self.subTest(speed=speed):

                self.pedestrian.walking_speed_mps = speed

                with self.assertRaises(ValueError):
                    move_pedestrian(
                        self.pedestrian,
                        self.graph,
                        dt=1.0,
                    )

class TestSignalWaiting(unittest.TestCase):
    """Regression coverage for time budgeting and persistent signal countdowns."""

    def setUp(self):
        TestPedestrianMovement.setUp(self)
        self.pedestrian.walking_speed_mps = 1.0

    def signal(self, name="S1", node="B", edges=("E1", "E2"), wait=10):
        return StaticActuator(name, "traffic_signal", node, 0, 0,
                              controlled_edges=edges, min_wait_time_s=0,
                              max_wait_time_s=90, default_wait_time_s=30,
                              properties={"status": "on", "wait_time_s": wait})

    def with_signals(self, *signals):
        return replace(self.graph, actuators={s.actuator_id: s for s in signals})

    # Inherited movement tests assume 1.4 m/s; do not re-run them at 1 m/s.

    def test_partial_wait_and_controller_change(self):
        graph = self.with_signals(self.signal())
        move_pedestrian(self.pedestrian, graph, 105)
        self.assertEqual(self.pedestrian.state, PedestrianState.WAITING)
        self.assertEqual(self.pedestrian.position_m, 0)
        self.assertEqual(self.pedestrian.signal_wait_remaining_sec, 5)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 5)
        self.assertEqual(self.pedestrian.total_travel_time_sec, 100)
        changed = self.with_signals(self.signal(wait=30))
        move_pedestrian(self.pedestrian, changed, 10)
        self.assertEqual(self.pedestrian.position_m, 5)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 10)
        self.assertEqual(self.pedestrian.state, PedestrianState.WALKING)
        self.assertEqual(self.pedestrian.waiting_signal_ids, ())

    def test_exact_release_does_not_wait_twice(self):
        graph = self.with_signals(self.signal())
        move_pedestrian(self.pedestrian, graph, 110)
        self.assertEqual(self.pedestrian.state, PedestrianState.WALKING)
        self.assertEqual(self.pedestrian.position_m, 0)
        move_pedestrian(self.pedestrian, graph, 1)
        self.assertEqual(self.pedestrian.position_m, 1)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 10)

    def test_arrival_counts_only_used_time(self):
        move_pedestrian(self.pedestrian, self.with_signals(self.signal()), 200)
        self.assertEqual(self.pedestrian.state, PedestrianState.ARRIVED)
        self.assertEqual(self.pedestrian.total_travel_time_sec, 150)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 10)

    def test_small_steps_equal_one_large_step(self):
        graph = self.with_signals(self.signal())
        small = replace(self.pedestrian, route=list(self.pedestrian.route))
        move_pedestrian(self.pedestrian, graph, 160)
        for _ in range(160):
            move_pedestrian(small, graph, 1)
        self.assertEqual(small, self.pedestrian)

    def test_reverse_crossing(self):
        agent = replace(self.pedestrian, origin="C", destination="A", route=["C", "B", "A"])
        move_pedestrian(agent, self.with_signals(self.signal()), 200)
        self.assertEqual(agent.total_wait_time_sec, 10)
        self.assertEqual(agent.state, PedestrianState.ARRIVED)

    def test_only_controlled_departures_wait(self):
        graph = self.with_signals(self.signal(edges=("E1",)))
        move_pedestrian(self.pedestrian, graph, 200)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 0)

    def test_origin_signal_and_zero_step(self):
        graph = self.with_signals(self.signal(node="A", edges=("E1",), wait=30))
        before = replace(self.pedestrian, route=list(self.pedestrian.route))
        move_pedestrian(self.pedestrian, graph, 0)
        self.assertEqual(self.pedestrian, before)
        move_pedestrian(self.pedestrian, graph, 10)
        self.assertEqual(self.pedestrian.signal_wait_remaining_sec, 20)
        self.assertEqual(self.pedestrian.total_travel_time_sec, 0)
        move_pedestrian(self.pedestrian, graph, 22)
        self.assertEqual(self.pedestrian.position_m, 2)

    def test_destination_has_no_departure_wait(self):
        move_pedestrian(self.pedestrian, self.with_signals(self.signal(node="C")), 200)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 0)

    def test_overlapping_signals_use_max_and_next_node_adds_wait(self):
        graph = self.with_signals(self.signal("S1", "A", ("E1",), 10),
                                  self.signal("S2", "B", ("E1", "E2"), 10),
                                  self.signal("S3", "B", ("E1", "E2"), 30))
        move_pedestrian(self.pedestrian, graph, 200)
        self.assertEqual(self.pedestrian.total_wait_time_sec, 40)
        self.assertEqual(self.pedestrian.state, PedestrianState.ARRIVED)

    def test_revisiting_signal_waits_once_per_visit(self):
        agent = replace(self.pedestrian, route=["A", "B", "A", "B", "C"])
        move_pedestrian(agent, self.with_signals(self.signal()), 500)
        self.assertEqual(agent.total_wait_time_sec, 20)
        self.assertEqual(agent.total_travel_time_sec, 350)

    def test_invalid_wait_rejected_before_movement(self):
        for wait in (-1, float("nan"), float("inf"), True, None):
            with self.subTest(wait=wait):
                before = replace(self.pedestrian, route=list(self.pedestrian.route))
                with self.assertRaises(ValueError):
                    move_pedestrian(self.pedestrian, self.with_signals(self.signal(wait=wait)), 200)
                self.assertEqual(self.pedestrian, before)


if __name__ == "__main__":
    unittest.main()