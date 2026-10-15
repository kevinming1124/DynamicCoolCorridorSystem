"""
Unit tests for pedestrian movement.

Verifies pedestrian movement along campus graph edges,
transitions between edges, destination arrival, and
validation of invalid movement conditions.
"""

import unittest

from models.pedestrian.agent import Pedestrian, PedestrianState
from models.pedestrian.movement import (
    get_edge_distance,
    move_pedestrian,
)

from utils.graph.graph import CampusGraph
from utils.graph.entities import GraphEdge, GraphNode


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

if __name__ == "__main__":
    unittest.main()