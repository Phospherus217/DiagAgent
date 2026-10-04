"""Public gates: measured fixtures, provenance, guard denials and session closure."""
import json
from pathlib import Path

from PIL import Image
import pytest

from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import load_trace
from diagagent.experiments.recovery_cases import MODES, measure_run, registered_cases, run_case
from diagagent.experiments.portfolio_protocol import ROOT, contract, sha
from diagagent.healing.guard import HealingGuard
from diagagent.healing.loop import HealingLoop
from diagagent.healing.policy import RecoveryPolicy
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.recovery import RecoverySession
from diagagent.tasks import load_task


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    root = tmp_path_factory.mktemp("public_cases")
    return {(entry["case_id"], mode): (output, run_case(entry["case_id"], mode, output))
            for entry in registered_cases() for mode in MODES
            for output in [root / entry["case_id"] / mode]}


@pytest.mark.parametrize("case_id,step", [("MATCHED-PARAMETER", 2), ("MATCHED-DIALOG", 2), ("MATCHED-FILEIO", 3)])
@pytest.mark.parametrize("mode", MODES)
def test_three_cases_all_modes(cases, case_id, step, mode):
    output, result = cases[case_id, mode]
    assert result == json.loads((output / "case_result.json").read_text(encoding="utf-8"))
    assert result["first_failure_step"] == step
    expected = mode == "constrained_recovery" or (mode == "direct_retry" and case_id != "MATCHED-PARAMETER")
    assert result["recovered"] is expected
    assert result["attempts"] == int(mode != "no_recovery")
    assert result["real_gui_acceptance"] is False
    if mode == "constrained_recovery":
        session = json.loads((output / result["recovery_session"] / "session.json").read_text(encoding="utf-8"))
        assert session["attempt_count"] == session["max_attempts"] == 1
        assert session["state"] == "recovered"
        assert session["guard_decision"]["decision"] == "ALLOW"
        assert session["proposed_repair"] and session["diagnosis"]
        assert session["execution_result"]["artifact_measurement"]["status"] == "PASS"
        assert session["verification_result"]["recovered"] is True
    else:
        assert result["guard_decisions"] == []


def test_trace_graph_has_connected_observed_evidence(cases):
    output, result = cases["MATCHED-PARAMETER", "no_recovery"]
    bundle = load_trace(output / result["original_run"])
    diagnosis = classify_bundle(bundle)
    graph = build_evidence_graph(bundle, diagnosis)
    assert bundle.public_task["instruction"] and diagnosis.first_failure_step == 2
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert {"Task Goal", "Action", "Execution Event", "State", "Artifact", "Failure", "Diagnosis"}.issubset(
        {node["type"] for node in nodes.values()})
    adjacency = {}
    for edge in graph["edges"]:
        assert edge["from"] in nodes and edge["to"] in nodes
        adjacency.setdefault(edge["from"], set()).add(edge["to"])
    def reachable(start):
        seen, pending = set(), [start]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(adjacency.get(current, []))
        return seen
    assert {"state_2_after", "failure_2", "diagnosis", "artifact_final"}.issubset(reachable("action_2"))
    event = next(node["id"] for node in nodes.values()
                 if node["type"] == "Execution Event" and node["evidence"].get("step") == 2)
    assert event in reachable("action_2") and "state_2_after" in reachable(event)
    assert "diagnosis" in adjacency["state_2_after"]
    assert nodes[event]["evidence"]["_source"] == "events.jsonl"
    for reference in diagnosis.evidence:
        assert (output / result["original_run"] / reference["source"]).is_file()


def test_checked_in_sample_is_portable_and_matches_actual_png():
    root = ROOT / "examples/public_sample"
    bundle = load_trace(root)
    assert bundle.public_task["instruction"]
    assert classify_bundle(bundle).first_failure_step == 2
    with Image.open(root / "artifacts/output.png") as actual:
        assert actual.size == (256, 256)
    assert bundle.artifact_result["status"] == "FAIL"
    for row in bundle.steps:
        for key in ("obs_before", "obs_after"):
            observation = row.get(key) or {}
            screenshot = observation.get("screenshot_path")
            if screenshot:
                assert not Path(screenshot).is_absolute()
                assert (root / screenshot).is_file()


def guard_inputs():
    diagnosis = {"diagnosis_id": "d", "first_failure_step": 2, "failure_type": "Parameter Error",
                 "confidence_score": .95, "responsibility": {"alternatives": []}}
    graph = {"nodes": [{"id": "action_2", "type": "Action", "evidence": {"step": 2}}]}
    task = {"allowed_actions": ["resize_image", "export_file", "click"],
            "initial_state": {"output_file": "output.png"}}
    state = {"repair_action": {"type": "resize_image", "width": 512, "height": 512},
             "reversible": True, "low_risk": True, "window_bbox": [0, 0, 1280, 720]}
    decision = RecoveryPolicy().decide(dict(diagnosis=diagnosis, evidence_graph=graph, task_spec=task, current_state=state))
    return dict(diagnosis=diagnosis, evidence_graph=graph, task_spec=task, decision=decision, current_state=state)


@pytest.mark.parametrize("change,code", [
    ({}, "ALLOW"),
    ({"repair_action": {"type": "resize_image"}}, "INVALID_ACTION"),
    ({"target_step": 3}, "WRONG_TARGET_STEP"),
    ({"repair_action": {"type": "wait", "duration": 0}}, "OUT_OF_SCOPE"),
    ({"repair_action": {"type": "export_file", "path": "../output.png"}}, "INVALID_OUTPUT_PATH"),
    ({"repair_action": {"type": "export_file", "path": "other.png"}}, "INVALID_OUTPUT_PATH"),
    ({"repair_action": {"type": "click", "x": 1400, "y": 10}}, "OUT_OF_SCOPE"),
    ({"existing_attempts": 1}, "BUDGET_EXCEEDED"),
])
def test_guard_reasons(change, code):
    inputs = guard_inputs()
    changes = dict(change)
    inputs["existing_attempts"] = changes.pop("existing_attempts", 0)
    inputs["decision"] = inputs["decision"].model_copy(update=changes)
    outcome = HealingGuard().check(**inputs)
    assert outcome["code"] == code and outcome["reason"]
    assert outcome["decision"] == ("ALLOW" if code == "ALLOW" else "DENY")


def test_denied_and_exhausted_session_never_executes(tmp_path):
    inputs = guard_inputs()
    calls = []
    def run(root, state):
        result = HealingLoop().run(run_context={"run_id": "r", "task_id": "t", "current_state": state},
            executor=lambda action: calls.append(action) or {"success": True},
            diagnosis=inputs["diagnosis"], evidence_graph=inputs["evidence_graph"],
            task_spec=inputs["task_spec"], run_dir=root)
        return result, json.loads((Path(result.session_path) / "session.json").read_text(encoding="utf-8"))
    denied, session = run(tmp_path / "denied", {**inputs["current_state"], "repair_action": {"type": "resize_image"}})
    assert denied.outcome == "GUARD_REJECTED" and session["state"] == "terminated"
    assert session["attempt_count"] == 0 and session["recovery_success"] is False and not calls
    failed, session = run(tmp_path / "budget", inputs["current_state"])
    assert failed.outcome == "NOT_RECOVERED" and session["attempt_count"] == 1
    denied, session = run(tmp_path / "budget", inputs["current_state"])
    assert session["guard_decision"]["code"] == "BUDGET_EXCEEDED"
    assert session["state"] == "terminated" and session["recovery_success"] is False and len(calls) == 1


def test_independent_verification_rechecks_actual_artifact(cases, tmp_path):
    import shutil
    output, result = cases["MATCHED-PARAMETER", "constrained_recovery"]
    replay = tmp_path / "replay"
    shutil.copytree(output / result["recovery_run"], replay)
    task = load_task(ROOT / "benchmark/tasks/resize_image.yaml")
    source_hash = sha(task["initial_state"]["input_file"])
    session = RecoverySession(run_id="r", task_id=task["task_id"], trigger_reason="test")
    measured = measure_run(replay, task, source_hash)
    assert RecoveryVerifier().verify(session=session, attempt=measured, contract=contract()).recovered
    Image.new("RGB", (256, 256)).save(replay / "artifacts/output.png")
    measured = measure_run(replay, task, source_hash)
    assert measured["process_recovered"] is True
    assert measured["artifact_recovered"] is False
    assert not RecoveryVerifier().verify(session=session, attempt=measured, contract=contract()).recovered
    assert not RecoveryVerifier().verify(session=session, attempt={"success": True}, contract=contract()).recovered
