"""run_simulation's per-round hook used by tools/make_animations.py."""
import contextlib
import io
import unittest
from pathlib import Path

from main import load_simulation_graph, run_simulation
from models.routing import TripRequest

USC = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


class TestOnRoundHook(unittest.TestCase):
    def test_hook_receives_each_round_with_stage_reports(self):
        calls = []
        graph, layout, _ = load_simulation_graph(USC, 4)
        trips = [TripRequest(1, "Olin Hall of Engineering", "Seeley G. Mudd Building")]
        with contextlib.redirect_stdout(io.StringIO()):
            final = run_simulation(
                graph, rounds=3, dataset_directory=USC, trip_requests=trips,
                context={"time": "11:00", "scenario": "hot", "pedestrian_dt_sec": 20.0,
                         "misting_scenario": layout},
                on_round=lambda n, time, output, reports: calls.append((n, time, output, reports)))
        self.assertEqual([c[0] for c in calls], [1, 2, 3])
        self.assertTrue(all(c[1] == "11:00" for c in calls))
        self.assertIs(calls[-1][2], final)
        reports = calls[0][3]
        for stage in ("control_model", "environment_model", "heat_exposure_model",
                      "routingc_model", "requirements_verification"):
            self.assertIn(stage, reports)
        self.assertEqual(reports["routingc_model"]["routes"][0]["route_policy"] in
                         ("safe_route", "coolest_fallback"), True)
        # Each round gets its own reports, not a shared mutable dictionary.
        self.assertIsNot(calls[0][3], calls[1][3])
        self.assertEqual(calls[1][3]["routingc_model"]["routes"], [])


if __name__ == "__main__":
    unittest.main()
