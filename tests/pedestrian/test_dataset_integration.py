"""USC movement integration: geometry, signals, occupancy, and saved snapshots."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from models.pedestrian.agent import PedestrianState
from models.pedestrian.demo import DEFAULT_DATASET_PATH, DEFAULT_ROUTES_PATH, main, run_standalone
from models.pedestrian.model import PedestrianSimulator
from models.pedestrian.movement import get_edge_distance, get_route_edge
from utils import ModelInput, load_campus_graph

DATASET_PATH = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


class TestDatasetIntegration(unittest.TestCase):
    def setUp(self):
        self.graph = load_campus_graph(DATASET_PATH)
        self.simulator = PedestrianSimulator(self.graph)
        self.routes = json.loads(DEFAULT_ROUTES_PATH.read_text())["pedestrians"]
        self.route = self.routes[0]["route"]

    def length(self, route):
        return sum(get_edge_distance(self.graph, a, b) for a, b in zip(route, route[1:]))

    def predict(self, dt, graph=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.simulator.predict(ModelInput(
                self.simulator.graph if graph is None else graph,
                context={"pedestrian_dt_sec": dt}))

    def test_dataset_and_signal_crossing_routes(self):
        self.assertEqual(DEFAULT_DATASET_PATH, DATASET_PATH)
        self.assertEqual(len(self.graph.nodes), 832)
        self.assertEqual(len(self.graph.edges), 930)
        self.assertEqual(self.routes[1]["route"], list(reversed(self.route)))
        for record in self.routes:
            self.simulator.assign_route(record["pedestrian_id"], record["route"])
            self.assertIn(self.graph.actuators["SIM_S2"].anchor_node, record["route"][1:-1])

    def test_single_edge_movement_bidirectional_arrival_and_no_extra_time(self):
        route = self.route[:2]  # Neither endpoint is a signal.
        distance = self.length(route)
        for pid, path in ((1, route), (2, list(reversed(route)))):
            self.simulator.assign_route(pid, path)
        self.predict(distance / 1.4 / 2)
        for pid in (1, 2):
            self.assertAlmostEqual(self.simulator.get_pedestrian(pid).position_m, distance / 2)
        self.predict(distance / 1.4)
        for pid in (1, 2):
            person = self.simulator.get_pedestrian(pid)
            self.assertEqual(person.state, PedestrianState.ARRIVED)
            self.assertAlmostEqual(person.total_travel_time_sec, distance / 1.4)
            self.assertEqual(person.total_wait_time_sec, 0)
        arrived = self.simulator.get_state()["pedestrians"]
        self.predict(50)
        self.assertEqual(self.simulator.get_state()["pedestrians"], arrived)

    def test_timestep_carries_across_segments(self):
        route = self.route[:3]
        first = get_edge_distance(self.graph, *route[:2])
        second = get_edge_distance(self.graph, *route[1:])
        self.simulator.assign_route(1, route)
        self.predict((first + second / 2) / 1.4)
        person = self.simulator.get_pedestrian(1)
        self.assertEqual(person.current_edge_index, 1)
        self.assertAlmostEqual(person.position_m, second / 2)

    def test_wait_countdown_survives_command_change_and_resumes(self):
        signal = self.graph.actuators["SIM_S2"]
        index = self.route.index(signal.anchor_node)
        self.simulator.assign_route(1, self.route)
        self.predict(self.length(self.route[:index + 1]) / 1.4 + 5)
        person = self.simulator.get_pedestrian(1)
        wait = signal.properties["wait_time_s"]
        self.assertEqual(person.state, PedestrianState.WAITING)
        self.assertEqual(person.current_edge_index, index)
        self.assertAlmostEqual(person.signal_wait_remaining_sec, wait - 5)
        changed = replace(self.simulator.graph, actuators={
            **self.simulator.graph.actuators,
            signal.actuator_id: signal.with_property("wait_time_s", 10)})
        self.predict(wait - 5, changed)
        self.predict(1)
        person = self.simulator.get_pedestrian(1)
        self.assertAlmostEqual(person.total_wait_time_sec, wait)
        self.assertEqual(person.signal_wait_remaining_sec, 0)
        self.assertEqual(person.state, PedestrianState.WALKING)
        self.assertAlmostEqual(person.position_m, 1.4)

    def test_full_route_times_and_timestep_independence(self):
        results = []
        for dt, steps in ((1, 100), (100, 1)):
            with contextlib.redirect_stdout(io.StringIO()):
                output = run_standalone(dt=dt, steps=steps)
            state = output.diagnostics["pedestrian_state"]
            results.append(state)
            self.assertEqual(state["summary"]["completed_trips"], 2)
            self.assertEqual(sum(output.property_values["pedestrian_count"].values()), 0)
            for person in state["pedestrians"]:
                expected_wait = 0
                for a, b in zip(person["route"], person["route"][1:]):
                    edge = get_route_edge(self.graph, a, b)
                    waits = [s.properties["wait_time_s"] for s in self.graph.actuators.values()
                             if s.actuator_type == "traffic_signal" and s.anchor_node == a
                             and edge.edge_id in s.controlled_edges]
                    expected_wait += max(waits, default=0)
                self.assertAlmostEqual(person["total_wait_time_sec"], expected_wait)
                self.assertAlmostEqual(person["total_travel_time_sec"], self.length(person["route"]) / 1.4)
        for a, b in zip(results[0]["pedestrians"], results[1]["pedestrians"]):
            self.assertEqual(a["state"], b["state"])
            self.assertAlmostEqual(a["total_elapsed_time_sec"], b["total_elapsed_time_sec"])

    def test_waiter_occupancy_and_graph_preservation(self):
        index = self.route.index(self.graph.actuators["SIM_S2"].anchor_node)
        route = self.route[index:]
        self.simulator.assign_route(1, route)
        self.simulator.assign_route(2, route, walking_speed_mps=1.0)
        tagged = self.graph.with_edge_property("test_marker", {e.edge_id: 7 for e in self.graph.edges})
        original = tagged.as_dict()
        output = self.predict(5, tagged)
        edge_id = get_route_edge(self.graph, *route[:2]).edge_id
        self.assertEqual(output.property_values["pedestrian_count"][edge_id], 2)
        self.assertEqual(sum(output.property_values["pedestrian_count"].values()), 2)
        self.assertEqual(len(output.property_values["pedestrian_count"]), 930)
        self.assertTrue(all(p.state == PedestrianState.WAITING for p in self.simulator.pedestrians.values()))
        self.assertEqual(tagged.as_dict(), original)
        self.assertTrue(all(e.properties["test_marker"] == 7 for e in output.graph.edges))

    def test_cli_saves_snapshots_waiting_and_arrivals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "movement.json"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--steps", "10", "--dt", "10", "--save", str(path)])
            saved = json.loads(path.read_text())
        snapshots = saved["diagnostics"]["snapshots"]
        self.assertEqual(len(snapshots), 11)
        self.assertEqual(snapshots[0]["simulation_time_sec"], 0)
        self.assertEqual(snapshots[-1]["summary"]["completed_trips"], 2)
        self.assertTrue(any(p["state"] == "waiting" for s in snapshots for p in s["pedestrians"]))
        for person in snapshots[-1]["pedestrians"]:
            self.assertAlmostEqual(person["distance_walked_m"], person["route_length_m"])

    def test_invalid_demo_options(self):
        for kwargs in ({"steps": 0}, {"steps": True}, {"dt": -1}, {"dt": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_standalone(**kwargs)


if __name__ == "__main__":
    unittest.main()
