import pytest

from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.recovery import RecoverySession, VerificationContract


def verify(data=None, **contract):
    payload = dict(process_recovered=True, artifact_recovered=True, task_recovered=True)
    payload.update(data or {})
    return RecoveryVerifier().verify(
        session=RecoverySession(run_id="test", task_id="resize", trigger_reason="failure"),
        attempt=payload, contract=VerificationContract(**contract))


def test_unknown_required_check_is_not_silently_accepted():
    result = verify(required_checks=["process", "semantic_text_correct"])
    assert not result.recovered
    assert "semantic_text_correct" in result.unmet_checks


@pytest.mark.parametrize("value", [None, False, "PASS", 1])
def test_custom_check_requires_explicit_boolean_true(value):
    assert not verify({"checks": {"action_match": value}},
                      required_checks=["action_match"]).recovered


def test_measured_custom_check_can_pass():
    assert verify({"checks": {"action_match": True}},
                  required_checks=["action_match"]).recovered


def test_unmeasured_side_effect_is_unknown_not_zero():
    result = verify(forbidden_side_effects=["unexpected_crop"])
    assert not result.recovered
    assert result.violated_side_effects == []
    assert "side_effect:unexpected_crop" in result.unmet_checks


def test_measured_side_effect_failure_and_pass():
    assert not verify({"side_effect_checks": {"unexpected_crop": False}},
                      forbidden_side_effects=["unexpected_crop"]).recovered
    assert verify({"side_effect_checks": {"unexpected_crop": True}},
                  forbidden_side_effects=["unexpected_crop"]).recovered


def test_narrow_contract_does_not_raise_or_accept_missing_core_checks():
    result = verify({"process_recovered": None}, required_checks=["artifact"])
    assert not result.recovered and "process" in result.unmet_checks


def test_expected_state_must_be_observed():
    assert not verify(expected_state_change={"width": 512}).recovered
    assert verify({"state_after": {"width": 512}},
                  expected_state_change={"width": 512}).recovered


def test_numeric_executor_status_is_not_a_boolean_measurement():
    assert not verify({"process_recovered": 1}).recovered
