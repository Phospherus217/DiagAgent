import json

import pytest

from diagagent.healing.guard import GuardViolation, HealingGuard
from diagagent.healing.loop import HealingLoop
from diagagent.healing.manager import RecoveryManager
from diagagent.healing.policy import RecoveryPolicy
from diagagent.healing.service import heal_run_dry_run
from diagagent.healing.state import HealingStateMachine, InvalidHealingTransition
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.diagnosis import DiagnosisResult
from diagagent.schemas.recovery import (
    HealingState, RecoveryPolicyDecision, RecoverySession, RecoveryVerificationResult,
    Repairability, VerificationContract,
)


def diagnosis_and_graph():
    diagnosis = DiagnosisResult(
        diagnosis_version="0.4", diagnosis_id="diag-1", run_id="run-1",
        failure=True, step=2, first_failure_step=2, type="Parameter Error",
        failure_type="Parameter Error", layer="Agent", confidence="high",
        confidence_score=0.95, explanation="Parameter mismatch",
    )
    graph = {"nodes": [
        {"id": "action_2", "type": "Action", "label": "resize_image"},
        {"id": "failure_2", "type": "Failure", "label": "Parameter Error"},
    ], "edges": [{"from": "action_2", "to": "failure_2", "relation": "observed_failure"}]}
    return diagnosis, graph


def test_state_machine_rejects_illegal_transition():
    machine = HealingStateMachine(HealingState.FAILURE_DETECTED)
    machine.transition(HealingState.DIAGNOSING)
    with pytest.raises(InvalidHealingTransition):
        machine.transition(HealingState.RECOVERED)


def test_policy_marks_supported_failure_repairable_but_requires_action_or_approval():
    diagnosis, graph = diagnosis_and_graph()
    decision = RecoveryPolicy().decide({
        "diagnosis": diagnosis, "evidence_graph": graph, "task_spec": {"allowed_actions": ["resize_image"]},
    })
    assert decision.repairability == Repairability.REPAIRABLE
    assert decision.repair_type == "parameter_update"
    assert decision.requires_human_approval is True
    assert decision.evidence_node_ids


def test_guard_rejects_missing_evidence_and_accepts_valid_explicit_action():
    diagnosis, graph = diagnosis_and_graph()
    base = RecoveryPolicy().decide({"diagnosis": diagnosis, "evidence_graph": graph,
                                    "task_spec": {"allowed_actions": ["resize_image"]}})
    with pytest.raises(GuardViolation, match="repair_action"):
        HealingGuard().validate(diagnosis=diagnosis, evidence_graph=graph, decision=base,
                                task_spec={"allowed_actions": ["resize_image"]})
    decision = base.model_copy(update={"repair_action": {"type": "resize_image", "width": 512, "height": 512},
                                       "requires_human_approval": False})
    HealingGuard().validate(diagnosis=diagnosis, evidence_graph=graph, decision=decision,
                            task_spec={"allowed_actions": ["resize_image"]})


def test_guard_rejects_wrong_export_path_and_budget():
    diagnosis, graph = diagnosis_and_graph()
    decision = RecoveryPolicy().decide({"diagnosis": diagnosis, "evidence_graph": graph,
                                        "task_spec": {"allowed_actions": ["export_file"]}}).model_copy(update={
        "repair_action": {"type": "export_file", "path": "outside.png"}, "requires_human_approval": False,
    })
    with pytest.raises(GuardViolation):
        HealingGuard().validate(diagnosis=diagnosis, evidence_graph=graph, decision=decision,
                                task_spec={"allowed_actions": ["export_file"], "initial_state": {"output_file": "output.png"}},
                                existing_attempts=1)


def test_recovery_verifier_does_not_trust_executor_success():
    session = RecoverySession(run_id="run", task_id="task", trigger_reason="failure")
    contract = VerificationContract(required_checks=["process", "artifact", "task"])
    failed = RecoveryVerifier().verify(session=session, attempt={"success": True, "process_recovered": True,
                                                                  "artifact_recovered": None, "task_success": True},
                                       contract=contract)
    assert failed.recovered is False and failed.artifact_recovered is None
    passed = RecoveryVerifier().verify(session=session, attempt={"process_recovered": True,
                                                                  "artifact_recovered": True, "task_success": True,
                                                                  "evidence_refs": ["after/state.json"]},
                                       contract=contract)
    assert passed.recovered is True and passed.confidence == 1.0


def test_manager_persists_session_events_and_requires_approval(tmp_path):
    diagnosis, graph = diagnosis_and_graph()
    manager = RecoveryManager(tmp_path)
    session = manager.start_session(run_id="run-1", task_id="task", trigger_step=2, trigger_reason="failure")
    manager.attach_diagnosis(diagnosis, graph)
    decision = RecoveryPolicy().decide({"diagnosis": diagnosis, "evidence_graph": graph,
                                        "task_spec": {"allowed_actions": ["resize_image"]}})
    manager.attach_policy(decision)
    assert manager.session.state == HealingState.AWAITING_APPROVAL
    with pytest.raises(ValueError):
        manager.attach_attempt("attempt-1")
    manager.approve(True)
    manager.attach_attempt("attempt-1")
    verification = RecoveryVerifier().verify(session=session, attempt={"process_recovered": True,
        "artifact_recovered": True, "task_success": True,
        "side_effect_checks": {name: True for name in decision.forbidden_side_effects}},
        contract=decision.verification_contract)
    manager.attach_verification(verification)
    assert manager.session.state == HealingState.RECOVERED
    assert (tmp_path / "recovery_sessions" / session.recovery_session_id / "recovery_events.jsonl").exists()


def test_healing_dry_run_never_executes_and_writes_audit(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "metadata.json").write_text(json.dumps({"task_id": "resize"}), encoding="utf-8")
    (run / "task_public.json").write_text(json.dumps({"task_id": "resize"}), encoding="utf-8")
    (run / "trace.jsonl").write_text(json.dumps({"step": 2, "action": {"type": "resize_image", "width": 128},
        "step_eval": {"error_type": "Parameter Error"}}) + "\n", encoding="utf-8")
    (run / "events.jsonl").write_text(json.dumps({"event": "run_finished", "success": False}) + "\n", encoding="utf-8")
    (run / "evaluation.json").write_text(json.dumps({"artifact": {"status": "FAIL"}}), encoding="utf-8")
    result = heal_run_dry_run(run)
    assert result.executed is False
    assert result.state == HealingState.AWAITING_APPROVAL
    session_dir = run / "recovery_sessions" / result.recovery_session_id
    assert (session_dir / "session.json").exists()
    assert (session_dir / "recovery_events.jsonl").exists()
    assert not (session_dir / "attempts").exists()


def test_explicit_run_executes_one_guarded_attempt_and_persists_verification(tmp_path):
    diagnosis, graph = diagnosis_and_graph()
    calls = []
    loop = HealingLoop()
    result = loop.run(
        run_context={"run_id": "run-1", "task_id": "task", "human_approved": True,
                     "current_state": {"reversible": True, "low_risk": True}},
        diagnosis=diagnosis, evidence_graph=graph,
        task_spec={"allowed_actions": ["resize_image"]},
        feedback={"repair_action": {"type": "resize_image", "width": 512, "height": 512}},
        run_dir=tmp_path,
        executor=lambda action: calls.append(action) or {
            "process_recovered": True, "artifact_recovered": True, "task_recovered": True,
            "side_effect_checks": {name: True for name in
                ("unexpected_crop", "unexpected_resize", "output_path_escape")},
            "evidence_refs": ["after/state.json"],
        },
    )
    assert result.executed is True and result.recovery_success is True
    assert len(calls) == 1
    attempt_files = list((tmp_path / "recovery_sessions" / result.recovery_session_id / "attempts").rglob("result.json"))
    assert len(attempt_files) == 1
    repeated = loop.run(
        run_context={"run_id": "run-1", "task_id": "task", "human_approved": True,
                     "current_state": {"reversible": True, "low_risk": True}},
        diagnosis=diagnosis, evidence_graph=graph,
        task_spec={"allowed_actions": ["resize_image"]},
        feedback={"repair_action": {"type": "resize_image", "width": 512, "height": 512}},
        run_dir=tmp_path, executor=lambda action: calls.append(action) or {},
    )
    assert repeated.outcome == "GUARD_REJECTED"
    assert not repeated.executed and len(calls) == 1
    assert (tmp_path / "recovery_attempt.claim.json").is_file()
