"""Unit and integration tests for DiagAgent v2.0 closed-loop runtime, models, verification, and recovery."""

import json
from pathlib import Path
from PIL import Image
import pytest

from diagagent.actions.schema import BaseAction
from diagagent.agent.context import ContextBuilder
from diagagent.agent.runtime import AgentRuntime
from diagagent.agent.state import RuntimeState
from diagagent.agent.termination import TerminationPolicy
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.models.base import Message, ModelResponse
from diagagent.models.mock import MockModel
from diagagent.models.openai_compatible import OpenAICompatibleModel
from diagagent.recovery.policy import RecoveryPolicy
from diagagent.verification.models import VerificationResult
from diagagent.verification.verifier import RuntimeVerifier


def test_mock_model_query_deterministic():
    """Verify MockModel returns deterministic configured responses."""
    responses = [
        '{"type": "resize_image", "width": 512, "height": 512}',
        '{"type": "export_file", "path": "output.png", "format": "png"}',
        '{"type": "stop"}',
    ]
    model = MockModel(responses=responses)
    msgs = [Message(role="user", content="Resize and export")]

    resp1 = model.query(msgs)
    assert "resize_image" in resp1.text
    assert resp1.provider == "mock"

    resp2 = model.query(msgs)
    assert "export_file" in resp2.text

    resp3 = model.query(msgs)
    assert "stop" in resp3.text

    # Default action fallback
    resp4 = model.query(msgs)
    assert "stop" in resp4.text
    assert model.query_count == 4


def test_context_builder_strict_anti_leakage():
    """Verify ContextBuilder never leaks evaluator-only state, reference traces, or gold labels."""
    builder = ContextBuilder()

    task_spec = {
        "task_id": "secret_task_01",
        "instruction": "Resize image to 512x512 and export PNG",
        "allowed_actions": ["resize_image", "export_file", "stop"],
        "initial_state": {
            "input_file": "dummy.png",
            "output_file": "output.png",
            "evaluator_only_secret": "DO_NOT_REVEAL_12345",
        },
        "gold_first_failure": "Parameter Error",
        "reference_trace": [{"action": "secret_action"}],
        "subgoals": [{"id": "sg1", "expected_state": {"hidden": True}}],
    }

    class FakeObservation:
        active_dialog = "Scale Image"
        active_tool = "zoom"
        window_focused = True
        evaluator_only_state = {"gold_answer": 42}
        screenshot_path = "screenshots/step_001.png"

    obs = FakeObservation()
    feedback = {
        "action_error": "Syntax error",
        "evaluator_score": 0.0,
        "gold_expected_tool": "text",
    }

    messages = builder.build_context(
        task_spec=task_spec,
        observation=obs,
        history=[{"step": 1, "action": {"type": "wait"}, "verification": {"passed": True}}],
        feedback=feedback,
    )

    full_text = "\n".join(m.content for m in messages)

    # Assert clean public task info exists
    assert "secret_task_01" in full_text
    assert "Resize image to 512x512 and export PNG" in full_text
    assert "Scale Image" in full_text

    # Assert STRICT absence of evaluator/gold leaked data
    assert "DO_NOT_REVEAL" not in full_text
    assert "gold_first_failure" not in full_text
    assert "reference_trace" not in full_text
    assert "secret_action" not in full_text
    assert "gold_answer" not in full_text
    assert "evaluator_score" not in full_text
    assert "gold_expected_tool" not in full_text


def test_runtime_verifier_signals():
    """Verify RuntimeVerifier detects execution failure, no-op, dialog signals, and artifacts."""
    verifier = RuntimeVerifier()

    # 1. Normal state change passes
    res_ok = verifier.verify(
        before=None,
        action={"type": "resize_image", "width": 512, "height": 512},
        execution={"success": True, "state_changed": True},
        after=None,
    )
    assert res_ok.passed is True
    assert res_ok.signals["state_changed"] is True

    # 2. Execution no-op detected when state does not change
    res_noop = verifier.verify(
        before=None,
        action={"type": "resize_image", "width": 512, "height": 512},
        execution={"success": True, "state_changed": False},
        after=None,
    )
    assert res_noop.passed is False
    assert res_noop.suspected_failure == "Execution No-op"
    assert "Execution No-op" in res_noop.evidence[0] or "without measurable" in res_noop.evidence[0]

    # 3. Execution failure detected
    res_fail = verifier.verify(
        before=None,
        action={"type": "click", "x": 100, "y": 100},
        execution={"success": False, "error": "Target outside canvas"},
        after=None,
    )
    assert res_fail.passed is False
    assert res_fail.suspected_failure == "Execution Error"


def test_recovery_policy_rules_and_budget():
    """Verify RecoveryPolicy enforces R1-R4 rules and bounded budgets."""
    policy = RecoveryPolicy(max_format_repairs=1, max_action_retries=1, max_replans=2)
    state = RuntimeState(
        run_id="test_run",
        task_id="test_task",
        retry_budget=1,
        repair_budget=1,
        replan_budget=2,
    )

    # R1: Format Repair
    verif_format = VerificationResult(passed=False, suspected_failure="Action Format Error")
    dec1 = policy.decide(verif_format, state, action_error="Unexpected comma")
    assert dec1.decision == "repair_format"
    assert state.repair_budget == 0

    # Exhausted R1 budget -> abort
    dec1_ex = policy.decide(verif_format, state, action_error="Unexpected comma")
    assert dec1_ex.decision == "abort"

    # R2: Execution No-op
    verif_noop = VerificationResult(passed=False, suspected_failure="Execution No-op")
    dec2 = policy.decide(verif_noop, state)
    assert dec2.decision == "reobserve"
    assert state.retry_budget == 0

    # Exhausted R2 budget -> abort
    dec2_ex = policy.decide(verif_noop, state)
    assert dec2_ex.decision == "abort"

    # R4: Parameter Error
    verif_param = VerificationResult(passed=False, suspected_failure="Parameter Error")
    dec4 = policy.decide(verif_param, state)
    assert dec4.decision == "replan"
    assert state.replan_budget == 1

    dec4_2 = policy.decide(verif_param, state)
    assert dec4_2.decision == "replan"
    assert state.replan_budget == 0

    dec4_ex = policy.decide(verif_param, state)
    assert dec4_ex.decision == "abort"


def test_closed_loop_model_runtime_success(tmp_path):
    """Verify complete closed-loop run with MockModel producing structured actions."""
    input_img = tmp_path / "input.jpg"
    Image.new("RGB", (640, 480), "blue").save(input_img)

    task_spec = {
        "task_id": "test_model_closed_loop",
        "instruction": "Resize image to 512x512 and export PNG",
        "initial_state": {
            "input_file": str(input_img),
            "output_file": "output.png",
            "window_size": [1280, 720],
        },
        "subgoals": [
            {
                "id": "resize_image",
                "order": 1,
                "type": "parameter",
                "expected_state": {"width": 512, "height": 512},
            },
            {
                "id": "export_png",
                "order": 2,
                "type": "artifact",
            },
        ],
        "success_criteria": {
            "final": {
                "output_exists": True,
                "output_format": "png",
                "output_size": [512, 512],
            }
        },
        "max_steps": 5,
    }

    # Runtime completes after resize -> export, before the scripted stop.
    model = MockModel(responses=[
        '{"type": "resize_image", "width": 512, "height": 512}',
        '{"type": "export_file", "path": "output.png", "format": "png"}',
        '{"type": "stop"}',
    ])

    env = GIMPEnvironment(backend="mock", run_root=tmp_path / "runs")
    runtime = AgentRuntime(env=env, model=model)

    try:
        report = runtime.run(task_spec=task_spec)
        assert report.success is True
        assert report.task_status == "PASS"
        assert report.artifact_status == "PASS"
        assert report.first_failure_step is None
        assert model.query_count == 2
    finally:
        env.close()


def test_closed_loop_model_format_repair_recovery(tmp_path):
    """Verify closed-loop model that emits a bad format recovers after feedback."""
    input_img = tmp_path / "input.jpg"
    Image.new("RGB", (640, 480), "blue").save(input_img)

    task_spec = {
        "task_id": "test_model_format_recovery",
        "instruction": "Export image to output.png",
        "initial_state": {
            "input_file": str(input_img),
            "output_file": "output.png",
            "window_size": [1280, 720],
        },
        "subgoals": [
            {
                "id": "export_png",
                "order": 1,
                "type": "artifact",
            },
        ],
        "success_criteria": {
            "final": {
                "output_exists": True,
                "output_format": "png",
            }
        },
        "max_steps": 5,
    }

    # Model emits malformed text on first call, then valid actions on subsequent calls
    model = MockModel(responses=[
        "Here is what I will do: export the image as png",  # Malformed JSON
        '{"type": "export_file", "path": "output.png", "format": "png"}',
        '{"type": "stop"}',
    ])

    env = GIMPEnvironment(backend="mock", run_root=tmp_path / "runs")
    runtime = AgentRuntime(env=env, model=model)

    try:
        report = runtime.run(task_spec=task_spec)
        assert report.success is True
        assert report.artifact_status == "PASS"
        # First query was malformed, second was export; no stop query needed.
        assert model.query_count == 2
    finally:
        env.close()


def test_termination_policy_detects_false_completion():
    """Verify TerminationPolicy flags immediate stop without doing work as false completion."""
    policy = TerminationPolicy()
    state = RuntimeState(run_id="r1", task_id="t1", step=1)
    task_spec = {
        "initial_state": {"output_file": "output.png"},
        "success_criteria": {"final": {"output_exists": True}},
    }

    # Model emits stop at step 1 without output file verified
    verif = VerificationResult(passed=True, signals={"output_file_exists": False})
    term, reason = policy.evaluate(state, task_spec, is_stop_action=True, runtime_verification=verif)
    assert term is True
    assert reason == "false_completion"
