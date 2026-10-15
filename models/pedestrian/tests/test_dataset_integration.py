"""
Integration Tests for Pedestrian Simulation

Verifies that the pedestrian simulator works with the team's
canonical tiny_campus_block dataset.

Tests graph loading, pedestrian movement, edge transitions,
and completed trips using actual campus graph data.
"""

import unittest
from pathlib import Path

from models.pedestrian.agent import Pedestrian, PedestrianState
from models.pedestrian.model import PedestrianSimulator
from utils.graph.json_loader import load_campus_graph


# Resolve the dataset relative to the repository root.
# This avoids depending on the terminal's working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DATASET_PATH = (
    PROJECT_ROOT / "dataset" / "tiny_campus_block"
)


class TestDatasetIntegration(unittest.TestCase):

    def setUp(self):
        """Load the canonical campus dataset before each test."""

        self.graph = load_campus_graph(DATASET_PATH)

        self.simulator = PedestrianSimulator(self.graph)

    def test_dataset_loads(self):
        """The campus graph loads with the expected structure."""

        self.assertEqual(len(self.graph.nodes), 9)
        self.assertEqual(len(self.graph.edges), 12)
        self.assertEqual(len(self.graph.actuators), 3)

    def test_single_edge_movement(self):
        """
        A pedestrian moves along E01 from N01 to N02.

        Edge distance: 40 meters
        Walking speed: 1.4 meters/second
        """

        pedestrian = Pedestrian(
            pedestrian_id=1,
            origin="N01",
            destination="N02",
            route=["N01", "N02"],
            walking_speed_mps=1.4,
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=10.0)

        result = self.simulator.get_pedestrian(1)

        self.assertAlmostEqual(result.position_m, 14.0)

        self.assertEqual(
            result.state,
            PedestrianState.WALKING,
        )

        self.assertEqual(result.current_edge_index, 0)

    def test_single_edge_arrival(self):
        """A pedestrian completes the 40-meter N01 to N02 route."""

        pedestrian = Pedestrian(
            pedestrian_id=1,
            origin="N01",
            destination="N02",
            route=["N01", "N02"],
            walking_speed_mps=1.4,
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=30.0)

        result = self.simulator.get_pedestrian(1)

        self.assertEqual(
            result.state,
            PedestrianState.ARRIVED,
        )

        self.assertAlmostEqual(
            result.total_travel_time_sec,
            40.0 / 1.4,
        )

        self.assertEqual(result.current_edge_index, 1)

    def test_multiple_edge_movement(self):
        """
        A pedestrian moves from N01 to N03 through N02.

        N01 -> N02: 40 meters
        N02 -> N03: 40 meters
        Total: 80 meters
        """

        pedestrian = Pedestrian(
            pedestrian_id=2,
            origin="N01",
            destination="N03",
            route=["N01", "N02", "N03"],
            walking_speed_mps=1.4,
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=30.0)

        result = self.simulator.get_pedestrian(2)

        # 1.4 m/s * 30 seconds = 42 meters.
        # The pedestrian should be 2 meters into E02.
        self.assertEqual(result.current_edge_index, 1)

        self.assertAlmostEqual(result.position_m, 2.0)

        self.assertEqual(
            result.state,
            PedestrianState.WALKING,
        )

    def test_multiple_edge_arrival(self):
        """A pedestrian completes the 80-meter route."""

        pedestrian = Pedestrian(
            pedestrian_id=2,
            origin="N01",
            destination="N03",
            route=["N01", "N02", "N03"],
            walking_speed_mps=1.4,
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=60.0)

        result = self.simulator.get_pedestrian(2)

        self.assertEqual(
            result.state,
            PedestrianState.ARRIVED,
        )

        self.assertAlmostEqual(
            result.total_travel_time_sec,
            80.0 / 1.4,
        )

        self.assertEqual(result.current_edge_index, 2)

    def test_bidirectional_movement(self):
        """Pedestrians can traverse dataset edges in reverse."""

        pedestrian = Pedestrian(
            pedestrian_id=3,
            origin="N02",
            destination="N01",
            route=["N02", "N01"],
            walking_speed_mps=1.4,
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=10.0)

        result = self.simulator.get_pedestrian(3)

        self.assertAlmostEqual(result.position_m, 14.0)

        self.assertEqual(
            result.state,
            PedestrianState.WALKING,
        )

    def test_multiple_pedestrians_on_dataset(self):
        """Multiple pedestrians move independently on the campus."""

        pedestrian_1 = Pedestrian(
            pedestrian_id=1,
            origin="N01",
            destination="N02",
            route=["N01", "N02"],
            walking_speed_mps=1.4,
        )

        pedestrian_2 = Pedestrian(
            pedestrian_id=2,
            origin="N02",
            destination="N03",
            route=["N02", "N03"],
            walking_speed_mps=1.0,
        )

        self.simulator.add_pedestrian(pedestrian_1)
        self.simulator.add_pedestrian(pedestrian_2)

        self.simulator.step(dt=10.0)

        result_1 = self.simulator.get_pedestrian(1)
        result_2 = self.simulator.get_pedestrian(2)

        self.assertAlmostEqual(result_1.position_m, 14.0)
        self.assertAlmostEqual(result_2.position_m, 10.0)

        self.assertAlmostEqual(
            self.simulator.simulation_time_sec,
            10.0,
        )

        state = self.simulator.get_state()

        self.assertEqual(
            state["summary"]["active_pedestrians"],
            2,
        )

    def test_graph_remains_unchanged(self):
        """Pedestrian movement does not modify the shared graph."""

        original_graph = self.graph.as_dict()

        pedestrian = Pedestrian(
            pedestrian_id=1,
            origin="N01",
            destination="N02",
            route=["N01", "N02"],
        )

        self.simulator.add_pedestrian(pedestrian)

        self.simulator.step(dt=10.0)

        self.assertEqual(
            self.graph.as_dict(),
            original_graph,
        )


if __name__ == "__main__":
    unittest.main()