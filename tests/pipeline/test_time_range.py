"""Daytime time-range clock: schedule, command-line resolution and round times."""
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from main import (
    build_time_schedule,
    load_simulation_graph,
    parse_arguments,
    run_simulation,
)
from models.environment import EnvironmentModel

USC = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"


def parse_quietly(argv):
    with contextlib.redirect_stderr(io.StringIO()):
        return parse_arguments(argv)


class TestBuildTimeSchedule(unittest.TestCase):
    def test_environment_refreshes_every_update_interval(self):
        schedule = build_time_schedule(8 * 60, 9 * 60, step_sec=600, update_min=30)
        self.assertEqual(schedule, ["08:00", "08:00", "08:00", "08:30", "08:30", "08:30"])

    def test_rounds_stop_before_end_and_cover_a_partial_last_step(self):
        self.assertEqual(len(build_time_schedule(480, 485, step_sec=120)), 3)
        self.assertEqual(len(build_time_schedule(480, 1020, step_sec=60)), 540)

    def test_invalid_inputs_raise(self):
        for kwargs in ({"step_sec": 0}, {"step_sec": -60}, {"step_sec": float("inf")},
                       {"step_sec": 60, "update_min": 0}, {"step_sec": 60, "update_min": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_time_schedule(480, 540, **kwargs)
        with self.assertRaises(ValueError):
            build_time_schedule(540, 480, step_sec=60)


class TestTimeRangeArguments(unittest.TestCase):
    def test_fixed_time_defaults_are_unchanged(self):
        arguments = parse_quietly([])
        self.assertEqual((arguments.time, arguments.rounds, arguments.pedestrian_dt), ("12:00", 1, 1.0))
        self.assertIsNone(arguments.time_schedule)

    def test_flag_alone_runs_the_whole_day_in_one_minute_steps(self):
        arguments = parse_quietly(["--time-range"])
        self.assertEqual(arguments.time_range, (8 * 60, 17 * 60))
        self.assertEqual((arguments.rounds, arguments.pedestrian_dt), (540, 60.0))
        schedule = arguments.time_schedule
        self.assertEqual((schedule[0], schedule[29], schedule[30], schedule[-1]),
                         ("08:00", "08:00", "08:30", "16:30"))

    def test_custom_range_timestep_interval_and_rounds(self):
        arguments = parse_quietly(["--time-range", "10:00-12:00", "--pedestrian-dt", "300",
                                   "--env-update-min", "60", "--rounds", "13"])
        self.assertEqual(arguments.rounds, 13)
        self.assertEqual(arguments.time_schedule[11:13], ["10:00", "11:00"])

    def test_invalid_combinations_exit(self):
        cases = [
            ["--time", "13:00", "--time-range"],              # two clocks
            ["--time-range", "09:00-08:00"],                   # ends before it starts
            ["--time-range", "05:00-09:00"],                   # before sunrise
            ["--time-range", "16:00-21:00"],                   # after sunset
            ["--time-range", "8-17"],                          # malformed
            ["--time-range", "--rounds", "541"],               # past the end time
            ["--time-range", "--pedestrian-dt", "0"],          # clock would never advance
            ["--time-range", "--env-update-min", "0"],
        ]
        for argv in cases:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                parse_quietly(argv)


class TestTimeRangeSimulation(unittest.TestCase):
    def test_each_round_runs_environment_at_its_scheduled_time(self):
        seen = []
        original = EnvironmentModel.predict

        def record(model, model_input):
            seen.append(model_input.context["time"])
            return original(model, model_input)

        schedule = ["08:00", "08:00", "08:30"]
        with patch.object(EnvironmentModel, "predict", autospec=True, side_effect=record), \
                contextlib.redirect_stdout(io.StringIO()):
            output = run_simulation(
                load_simulation_graph(USC)[0], rounds=3,
                context={"scenario": "hot", "pedestrian_dt_sec": 600.0},
                trip_requests=[], dataset_directory=USC, time_schedule=schedule,
            )
        # Initialization, then one Environment call per round.
        self.assertEqual(seen, ["08:00", "08:00", "08:00", "08:30"])
        self.assertEqual(output.diagnostics["pedestrian_state"]["simulation_time_sec"], 1800)

    def test_rounds_beyond_schedule_raise(self):
        with self.assertRaisesRegex(ValueError, "exceed the time schedule"):
            run_simulation(load_simulation_graph(USC)[0], rounds=2, time_schedule=["08:00"],
                           trip_requests=[], dataset_directory=USC)


if __name__ == "__main__":
    unittest.main()
