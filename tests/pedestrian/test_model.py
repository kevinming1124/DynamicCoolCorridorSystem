"""
Unit Tests for Pedestrian Simulation Coordinator

Verifies pedestrian registration, simulation time progression,
multiple-agent movement, completed trips, state isolation,
and atomic simulation updates.

Tests use a small synthetic CampusGraph to isolate the
pedestrian simulator from the full system pipeline.
"""

import unittest
from dataclasses import replace

from models.pedestrian.agent import Pedestrian, PedestrianState
from models.pedestrian.model import PedestrianSimulator

from utils.graph.graph import CampusGraph
from utils import ModelInput
from utils.graph.entities import GraphEdge, GraphNode, StaticActuator


class TestPedestrianSimulator(unittest.TestCase):

    def setUp(self):
        """
        Create a three-node graph for testing.

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

        self.simulator = PedestrianSimulator(self.graph)

        self.pedestrian = Pedestrian(
            pedestrian_id=1,
            origin="A",
            destination="C",
            route=["A", "B", "C"],
            walking_speed_mps=1.4,
        )

    def test_pipeline_preserves_wait_and_occupancy_across_graph_updates(self):
        signal = StaticActuator("S", "traffic_signal", "A", 0, 0,
                                controlled_edges=("E1",), min_wait_time_s=0,
                                max_wait_time_s=90, properties={"wait_time_s": 30})
        graph = replace(self.graph, actuators={"S": signal})
        self.simulator.add_pedestrian(self.pedestrian)
        first = self.simulator.predict(ModelInput(graph, {"pedestrian_dt_sec": 10}))
        state = first.diagnostics["pedestrian_state"]["pedestrians"][0]
        self.assertEqual(state["signal_wait_remaining_sec"], 20)
        self.assertEqual(state["waiting_signal_ids"], ["S"])
        self.assertEqual(first.property_values["pedestrian_count"]["E1"], 1)
        # The latest command cannot restart a delay already in progress.
        graph = replace(first.graph, actuators={"S": replace(signal, properties={"wait_time_s": 90})})
        self.simulator.predict(ModelInput(graph, {"pedestrian_dt_sec": 25}))
        agent = self.simulator.get_pedestrian(1)
        self.assertEqual(agent.signal_wait_remaining_sec, 0)
        self.assertEqual(agent.total_wait_time_sec, 30)
        self.assertAlmostEqual(agent.position_m, 7)
        self.assertEqual(state["signal_wait_remaining_sec"], 20)  # independent snapshot

    def test_failed_signal_step_is_atomic(self):
        self.simulator.add_pedestrian(self.pedestrian)
        self.simulator.add_pedestrian(replace(self.pedestrian, pedestrian_id=2, route=["A", "B", "C"]))
        before = self.simulator.get_state()
        signal = StaticActuator("S", "traffic_signal", "B", 0, 0,
                                controlled_edges=("E2",), properties={"wait_time_s": -1})
        with self.assertRaises(ValueError):
            self.simulator.predict(ModelInput(replace(self.graph, actuators={"S": signal}),
                                              {"pedestrian_dt_sec": 100}))
        self.assertEqual(self.simulator.get_state(), before)
        self.assertIs(self.simulator.graph, self.graph)

    def test_route_replacement_cannot_erase_an_active_signal_wait(self):
        signal = StaticActuator("S", "traffic_signal", "A", 0, 0,
                                controlled_edges=("E1",), properties={"wait_time_s": 30})
        self.simulator.graph = replace(self.graph, actuators={"S": signal})
        self.simulator.add_pedestrian(self.pedestrian)
        self.simulator.step(10)
        before = self.simulator.get_state()
        with self.assertRaises(ValueError):
            self.simulator.assign_route(1, ["A", "B", "C"])
        self.assertEqual(self.simulator.get_state(), before)

    def test_add_pedestrian(self):
        """A valid pedestrian can be registered."""

        self.simulator.add_pedestrian(self.pedestrian)

        registered = self.simulator.get_pedestrian(1)

        self.assertEqual(registered.pedestrian_id, 1)
        self.assertEqual(registered.origin, "A")
        self.assertEqual(registered.destination, "C")

        self.assertEqual(
            registered.state,
            PedestrianState.WALKING,
        )

    def test_duplicate_pedestrian_id(self):
        """Duplicate pedestrian IDs must be rejected."""

        self.simulator.add_pedestrian(self.pedestrian)

        duplicate = Pedestrian(
            pedestrian_id=1,
            origin="A",
            destination="B",
            route=["A", "B"],
        )

        with self.assertRaises(ValueError):
            self.simulator.add_pedestrian(duplicate)

        self.assertEqual(len(self.simulator.pedestrians), 1)

    def test_invalid_route(self):
        """Routes containing nonexistent edges are rejected."""

        invalid_pedestrian = Pedestrian(
            pedestrian_id=2,
            origin="A",
            destination="C",
            route=["A", "C"],
        )

        with self.assertRaises(ValueError):
            self.simulator.add_pedestrian(invalid_pedestrian)

        self.assertEqual(len(self.simulator.pedestrians), 0)

    def test_multiple_pedestrians(self):
        """Multiple pedestrians advance independently."""

        pedestrian_2 = Pedestrian(
            pedestrian_id=2,
            origin="A",
            destination="B",
            route=["A", "B"],
            walking_speed_mps=1.0,
        )

        self.simulator.add_pedestrian(self.pedestrian)
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

    def test_simulation_clock(self):
        """The simulation clock advances once per timestep."""

        self.simulator.add_pedestrian(self.pedestrian)

        self.simulator.step(dt=1.0)
        self.simulator.step(dt=2.0)
        self.simulator.step(dt=3.0)

        self.assertAlmostEqual(
            self.simulator.simulation_time_sec,
            6.0,
        )

        pedestrian = self.simulator.get_pedestrian(1)

        self.assertAlmostEqual(
            pedestrian.position_m,
            1.4 * 6.0,
        )

    def test_completed_trip(self):
        """Completed pedestrians remain available for reporting."""

        self.simulator.add_pedestrian(self.pedestrian)

        self.simulator.step(dt=120.0)

        pedestrian = self.simulator.get_pedestrian(1)

        self.assertEqual(
            pedestrian.state,
            PedestrianState.ARRIVED,
        )

        self.assertEqual(
            pedestrian.current_edge_index,
            2,
        )

        self.assertAlmostEqual(
            pedestrian.total_travel_time_sec,
            150.0 / 1.4,
        )

        state = self.simulator.get_state()

        self.assertEqual(
            state["summary"]["completed_trips"],
            1,
        )

        self.assertEqual(
            state["summary"]["active_pedestrians"],
            0,
        )

    def test_registration_copies_pedestrian(self):
        """External modifications cannot change registered agents."""

        self.simulator.add_pedestrian(self.pedestrian)

        self.pedestrian.position_m = 75.0
        self.pedestrian.route[1] = "C"

        registered = self.simulator.get_pedestrian(1)

        self.assertAlmostEqual(registered.position_m, 0.0)

        self.assertEqual(
            registered.route,
            ["A", "B", "C"],
        )

    def test_get_pedestrian_returns_copy(self):
        """Retrieved pedestrians cannot modify internal state."""

        self.simulator.add_pedestrian(self.pedestrian)

        retrieved = self.simulator.get_pedestrian(1)

        retrieved.position_m = 50.0
        retrieved.route[1] = "C"

        original = self.simulator.get_pedestrian(1)

        self.assertAlmostEqual(original.position_m, 0.0)

        self.assertEqual(
            original.route,
            ["A", "B", "C"],
        )

    def test_state_snapshot_is_independent(self):
        """Modifying a state snapshot does not change the simulator."""

        self.simulator.add_pedestrian(self.pedestrian)

        snapshot = self.simulator.get_state()

        snapshot["pedestrians"][0]["position_m"] = 99.0
        snapshot["pedestrians"][0]["route"][1] = "C"

        pedestrian = self.simulator.get_pedestrian(1)

        self.assertAlmostEqual(pedestrian.position_m, 0.0)

        self.assertEqual(
            pedestrian.route,
            ["A", "B", "C"],
        )

    def test_atomic_step_rollback(self):
        """
        A failed movement update must not partially update
        pedestrians or advance the simulation clock.
        """

        pedestrian_2 = Pedestrian(
            pedestrian_id=2,
            origin="A",
            destination="B",
            route=["A", "B"],
        )

        self.simulator.add_pedestrian(self.pedestrian)
        self.simulator.add_pedestrian(pedestrian_2)

        # Deliberately corrupt the second pedestrian's state
        # after registration to simulate a runtime failure.
        self.simulator.pedestrians[2].position_m = 150.0

        original_time = self.simulator.simulation_time_sec
        original_position = self.simulator.get_pedestrian(1).position_m

        with self.assertRaises(ValueError):
            self.simulator.step(dt=1.0)

        self.assertAlmostEqual(
            self.simulator.simulation_time_sec,
            original_time,
        )

        self.assertAlmostEqual(
            self.simulator.get_pedestrian(1).position_m,
            original_position,
        )

        # The invalid pedestrian remains unchanged as well.
        self.assertAlmostEqual(
            self.simulator.get_pedestrian(2).position_m,
            150.0,
        )

    def test_invalid_timestep(self):
        """Invalid timesteps must not modify simulation state."""

        self.simulator.add_pedestrian(self.pedestrian)

        invalid_timesteps = [
            -1.0,
            float("nan"),
            float("inf"),
            float("-inf"),
        ]

        for dt in invalid_timesteps:
            with self.subTest(dt=dt):

                with self.assertRaises(ValueError):
                    self.simulator.step(dt=dt)

                self.assertAlmostEqual(
                    self.simulator.simulation_time_sec,
                    0.0,
                )

                self.assertAlmostEqual(
                    self.simulator.get_pedestrian(1).position_m,
                    0.0,
                )

    def test_empty_simulation(self):
        """Simulation time advances even without pedestrians."""

        self.simulator.step(dt=10.0)

        self.assertAlmostEqual(
            self.simulator.simulation_time_sec,
            10.0,
        )

        state = self.simulator.get_state()

        self.assertEqual(
            state["summary"]["total_pedestrians"],
            0,
        )

        self.assertEqual(
            state["summary"]["completed_trips"],
            0,
        )


if __name__ == "__main__":
    unittest.main()