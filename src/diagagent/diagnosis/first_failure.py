"""Observed failure localization without inferring action causes from final artifacts."""
from diagagent.diagnosis.taxonomy import get_precedence_rank


def detect_first_failure(bundle):
    """Return the detailed-design localization record with source evidence."""
    from diagagent.diagnosis.classifier import classify_bundle
    result = classify_bundle(bundle)
    return {"diagnosis_id": result.diagnosis_id, "run_id": result.run_id,
            "first_failure_step": result.first_failure_step, "failure_type": result.failure_type,
            "status": result.diagnosis_status, "evidence": result.evidence,
            "warnings": result.warnings,
            "localization_scope": "earliest_observed_failure; causal irrecoverability is not established"}


def step_status(row):
    if row.get("status") in {"PASS", "FAIL", "UNKNOWN"}:
        return row["status"]
    if row.get("error_type"):
        return "FAIL"
    return "PASS" if row.get("subgoal_completed") is True else "UNKNOWN"


class FirstFailureLocator:
    @classmethod
    def locate(cls, step_evaluations, artifact_eval=None, task_spec=None):
        candidates = [(i, row) for i, row in enumerate(step_evaluations)
                      if row.get("error_type") or step_status(row) == "FAIL"]
        if candidates:
            _, row = min(candidates, key=lambda pair: (
                pair[1].get("step", pair[0]),
                0 if (pair[1].get("evidence") or {}).get("error_message") else 1,
                get_precedence_rank(pair[1].get("error_type")), pair[0]))
            return dict(first_failure_step=row.get("step"), first_failure_subgoal=row.get("subgoal_id"),
                failure_type=row.get("error_type"), evidence=row.get("evidence", {}), note=row.get("note"),
                attribution_status="supported" if row.get("error_type") else "unknown")
        if artifact_eval and (artifact_eval.get("status") == "FAIL" or
                artifact_eval.get("status") is None and artifact_eval.get("artifact_pass") is False):
            return dict(first_failure_step=None, first_failure_subgoal="artifact_valid",
                failure_type="Artifact Error", evidence=artifact_eval.get("metrics", {}),
                note="Detected at final evaluation; causal action is not established.", attribution_status="supported")
        if artifact_eval and artifact_eval.get("error_type") == "Evaluator Error":
            return dict(first_failure_step=None, failure_type="Evaluator Error",
                        evidence=artifact_eval.get("metrics", {}), attribution_status="supported")
        return dict(first_failure_step=None, failure_type=None, evidence={}, attribution_status="unknown")
