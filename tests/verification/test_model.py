"""USC audit integration and deliberate evidence-corruption regression tests."""
import contextlib
import copy
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from main import load_simulation_graph, run_simulation
from models.routing import load_trip_requests
from models.verification.model import VerificationModel
from models.verification.checks import RouteNetwork
from utils import ModelInput, load_campus_graph


class TestVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = []
        original = VerificationModel.predict

        def capture(model, model_input):
            cls.inputs.append(ModelInput(model_input.graph, copy.deepcopy(model_input.context)))
            return original(model, model_input)

        with patch.object(VerificationModel, "predict", capture), contextlib.redirect_stdout(io.StringIO()):
            dataset = Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed"
            trips = dataset.parents[1] / "models" / "routing" / "example_signal_trips.json"
            graph, layout, _ = load_simulation_graph(dataset, 4)
            cls.result = run_simulation(
                graph, rounds=3, dataset_directory=dataset,
                trip_requests=load_trip_requests(trips),
                context={"time": "13:00", "scenario": "hot", "pedestrian_dt_sec": 30.0,
                         "misting_scenario": layout},
            )

    def verify(self, model_input, model=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return (model or VerificationModel.from_config()).predict(model_input)

    def test_usc_pipeline_and_arrival_history(self):
        output = self.result
        self.assertEqual(output.diagnostics["misting_scenario"]["misting_count"], 4)
        self.assertEqual(len(output.graph.edges), len(load_simulation_graph(
            Path(__file__).resolve().parents[2] / "dataset" / "usc_osm_trimmed", 4)[0].edges))
        self.assertEqual(len(self.inputs), 3)
        self.assertEqual(output.diagnostics["verification"]["simulation_status"], "PASS")
        self.assertEqual(len(output.diagnostics["verification"]["round_history"]), 3)
        state = output.diagnostics["pedestrian_state"]
        self.assertEqual(state["summary"]["completed_trips"], 2)
        self.assertTrue(all(p["total_wait_time_sec"] == 10 for p in state["pedestrians"]))
        self.assertTrue(any(p["signal_wait_remaining_sec"] > 0
                            for p in self.inputs[0].context["stage_diagnostics"]["pedestrian_simulation"]["pedestrian_state"]["pedestrians"]))

    def test_verifier_preserves_inputs_and_commands(self):
        evidence = self.inputs[0]
        context = copy.deepcopy(evidence.context)
        graph = evidence.graph.as_dict()
        output = self.verify(evidence)
        self.assertEqual(evidence.graph.as_dict(), graph)
        self.assertEqual(evidence.context, context)
        self.assertEqual(output.graph.actuators, evidence.graph.actuators)
        for before, after in zip(evidence.graph.edges, output.graph.edges):
            for key, value in before.properties.items():
                self.assertEqual(after.properties[key], value)

    def test_detects_wrong_cooling(self):
        evidence = self.inputs[0]
        first = evidence.graph.edges[0]
        graph = evidence.graph.with_edge_property("mist_cooling_f", {
            edge.edge_id: edge.properties["mist_cooling_f"] + (1 if edge == first else 0)
            for edge in evidence.graph.edges})
        output = self.verify(ModelInput(graph, evidence.context))
        audit = output.diagnostics["verification"]
        self.assertEqual(audit["actuator_response"]["misting_status"], "FAIL")
        self.assertTrue(any("mist_cooling_f" in message for message in audit["actuator_response"]["violations"]))

    def test_detects_inconsistent_signal_wait(self):
        evidence = self.inputs[0]
        context = copy.deepcopy(evidence.context)
        movement = next(report for report in context["stage_diagnostics"].values() if "pedestrian_state" in report)
        movement["pedestrian_state"]["pedestrians"][0]["total_wait_time_sec"] += 1
        output = self.verify(ModelInput(evidence.graph, context))
        audit = output.diagnostics["verification"]["actuator_response"]
        self.assertEqual(audit["signal_movement_status"], "FAIL")
        self.assertTrue(any("total_wait_time_sec" in message for message in audit["violations"]))

    def test_detects_unsafe_selected_route_with_safe_alternative_and_keeps_failure_history(self):
        evidence = self.inputs[0]
        graph = evidence.graph
        # Controlled heat evidence on real USC topology isolates the policy.
        for name, value in (("utci_c", 25.0), ("unsafe_heat", 0.0), ("heat_exposure_rate", 0.0)):
            graph = graph.with_edge_property(name, {edge.edge_id: value for edge in graph.edges})
        person = evidence.context["pedestrian_before_movement"]["pedestrians"][0]
        network = RouteNetwork(graph)
        selected = [network.edges_by_pair[frozenset((a, b))].edge_id
                    for a, b in zip(person["route"], person["route"][1:])]
        violation_graph = None
        for identifier in selected:
            candidate = graph
            for name, hot, safe in (("utci_c", 39.0, 25.0), ("unsafe_heat", 1.0, 0.0),
                                     ("heat_exposure_rate", 13.0, 0.0)):
                candidate = candidate.with_edge_property(name, {
                    edge.edge_id: hot if edge.edge_id == identifier else safe for edge in candidate.edges})
            if RouteNetwork(candidate).search(person["origin"], person["destination"],
                                               person["walking_speed_mps"], mode="time", safe_only=True) is not None:
                violation_graph = candidate
                break
        self.assertIsNotNone(violation_graph, "USC route must have an alternative for this policy check")
        verifier = VerificationModel.from_config()
        failed = self.verify(ModelInput(violation_graph, evidence.context), verifier)
        audit = failed.diagnostics["verification"]
        self.assertEqual(audit["route_selection_status"], "FAIL")
        self.assertTrue(any(r.get("route_policy_outcome") == "POLICY_VIOLATION" for r in audit["before_movement"]))
        later = self.verify(self.inputs[-1], verifier)
        self.assertEqual(later.diagnostics["verification"]["simulation_status"], "FAIL")

    def test_missing_evidence_is_an_input_error(self):
        evidence = self.inputs[0]
        with self.assertRaisesRegex(ValueError, "stage_diagnostics"):
            self.verify(ModelInput(evidence.graph))
        context = copy.deepcopy(evidence.context)
        del context["pedestrian_before_movement"]
        with self.assertRaisesRegex(ValueError, "before_movement"):
            self.verify(ModelInput(evidence.graph, context))


if __name__ == "__main__":
    unittest.main()
