"""Evidence-backed first failure, separate from task success and benchmark scoring."""
from diagagent.diagnosis.responsibility import attribute


def classify(trace, events=(), report=None):
    from diagagent.diagnosis.trace_loader import TraceBundle
    if isinstance(trace, TraceBundle):
        return classify_bundle(trace)
    report = report or {}
    candidates = []
    def add(step, kind, source, evidence, confidence="high"):
        if kind:
            candidates.append({"step": step, "raw_type": kind, "source": source,
                               "evidence": evidence, "confidence": confidence})
    for row in trace:
        evaluation = row.get("step_eval") or {}
        add(row.get("step"), row.get("error_type") or evaluation.get("error_type"),
            "trace.jsonl", evaluation.get("evidence") or row.get("execution_result"))
    for row in events:
        name = row.get("event")
        if name == "model_error":
            error = row.get("model_error") or {}
            add(row.get("step"), error.get("failure_type", "Model API Error"), "events.jsonl:model_error", error)
        elif name == "action_parsed" and row.get("status") == "FAILED":
            add(row.get("step"), "Action Format Error", "events.jsonl:action_parsed", row.get("error_message"))
        elif name == "runtime_verification":
            verification = row.get("verification") or {}
            if verification.get("passed") is False:
                add(row.get("step"), verification.get("suspected_failure"), "events.jsonl:runtime_verification",
                    verification.get("evidence"), "medium")
    if report.get("failure_type"):
        add(report.get("first_failure_step"), report["failure_type"], "diagnosis:benchmark", report.get("evidence"))
    if report.get("artifact_status") == "FAIL":
        stops = [r for r in trace if (r.get("action") or {}).get("type") == "stop"]
        earlier_artifact_failure = any(
            r.get("event") == "artifact_completion_checked"
            and (r.get("artifact_evaluation") or {}).get("status") == "FAIL" for r in events)
        if stops and not earlier_artifact_failure:
            add(stops[0].get("step"), "False Completion", "trace.jsonl:stop + artifact evaluation",
                {"artifact_status": "FAIL", "stop": stops[0]["action"]})
    first = min(candidates, key=lambda c: (c["step"] if isinstance(c["step"], int) else float("inf"),
                 0 if c["confidence"] == "high" else 1)) if candidates else None
    kind, owner, boundary = attribute(first["raw_type"] if first else None)
    return {"diagnosis_version": "0.2", "failure": bool(first),
            "step": first["step"] if first else None, "type": kind, "layer": owner,
            "confidence": first["confidence"] if first and owner else "unknown",
            "v02_attribution": {"failure_layer": boundary, "responsibility": owner,
                                "source": first["source"] if first else None,
                                "evidence": first["evidence"] if first else None},
            "diagnosis_status": "FAILURE_OBSERVED" if first else
                "NO_FAILURE_OBSERVED" if report.get("success") is True else "INSUFFICIENT_EVIDENCE"}


def classify_bundle(bundle):
    """Detailed-design classifier; keeps legacy benchmark fields untouched.

    Reports the earliest *observed* failure, including recovered failures.
    An artifact failure alone cannot identify the causal action.
    """
    import hashlib
    import json
    from pydantic import ValidationError
    from diagagent.actions.parser import ActionParser
    from diagagent.actions.schema import ACTION_MODEL_MAP
    from diagagent.core.errors import ActionParseError
    from diagagent.schemas.diagnosis import DiagnosisResult

    candidates = []
    def add(row, kind, source=None, content=None, confidence="high"):
        step = row.get("step")
        step = step if isinstance(step, int) and not isinstance(step, bool) else None
        candidates.append(dict(step=step, kind=kind, confidence=confidence, evidence={
            "source": source or row.get("_source", "trace.jsonl"),
            "line": row.get("_line"),
            "content": content if content is not None else {k: v for k, v in row.items() if not k.startswith("_")},
        }))

    def action_error(raw):
        if isinstance(raw, str):
            try:
                raw = ActionParser.extract_json(raw)
            except ActionParseError:
                return "Action Format Error"
        if not isinstance(raw, dict) or raw.get("type") not in ACTION_MODEL_MAP:
            return "Action Format Error"
        try:
            ACTION_MODEL_MAP[raw["type"]](**raw)
        except ValidationError:
            return "Parameter Error"
        return None

    received = {}
    for row in bundle.events:
        name = row.get("event", "")
        step = row.get("step")
        if name == "action_received":
            received[step] = row.get("raw_action")
            raw_action = row.get("raw_action")
            kind = action_error(raw_action) if raw_action is not None else None
            if kind:
                add(row, kind)
        elif name == "model_error":
            add(row, "Model API Error")
        elif name in {"action_parsed", "action_validated"} and row.get("status") == "FAILED":
            raw = row.get("raw_action", received.get(step))
            # A parser's explicit validation message also establishes known type.
            message = row.get("error_message", "")
            kind = action_error(raw) if raw is not None else (
                "Parameter Error" if message.startswith("Validation failed for action '") else "Action Format Error")
            add(row, kind or row.get("error_type") or "Action Format Error")
        elif name == "runtime_verification":
            verification = row.get("verification") or {}
            if verification.get("passed") is False:
                add(row, verification.get("suspected_failure"), confidence="medium")
        elif row.get("error_type") or name == "execution_error":
            add(row, row.get("error_type") or "Execution Error")

    for row in bundle.steps:
        evaluation = row.get("step_eval") or {}
        execution = row.get("execution_result") or {}
        kind = row.get("error_type") or evaluation.get("error_type") or execution.get("error_type")
        action = row.get("action")
        if kind == "Action Format Error" and action:
            kind = action_error(action) or kind
        if kind or evaluation.get("status") == "FAIL" or execution.get("success") is False:
            add(row, kind or ("Execution Error" if execution.get("success") is False else None),
                content={"error_type": kind, "step_eval": evaluation, "execution_result": execution})

    artifact = bundle.artifact_result
    if artifact.get("status") == "FAIL" or artifact.get("artifact_pass") is False and artifact.get("status") != "UNKNOWN":
        add({}, "Artifact Error", "evaluation.json" if "evaluation.json" in bundle.source_hashes else "artifact_result.json", artifact)
    elif artifact.get("error_type") == "Evaluator Error":
        add({}, "Evaluator Error", "evaluation.json", artifact)
    first = min(candidates, key=lambda c: (c["step"] if c["step"] is not None else float("inf"),
                c["confidence"] != "high")) if candidates else None
    raw_kind = first["kind"] if first else None
    _, owner, boundary = attribute(raw_kind)
    # Use the detailed design's vocabulary; legacy classify(list) keeps its aliases.
    kind = "Model API Error" if raw_kind in {"API Error", "Authentication Error"} else raw_kind
    if kind in {"Dialog Operation Error", "Dialog Error"}:
        owner, boundary = "Execution", "EXECUTION"
    suggestions = {
        "Parameter Error": "Regenerate action with required, valid parameters.",
        "Action Format Error": "Regenerate a supported action JSON object.",
        "Model API Error": "Restore model service access before an explicit new attempt.",
        "Artifact Error": "Inspect the recorded artifact checks before planning repair.",
    }
    confidence_score = 0.92 if first and first["confidence"] == "high" else 0.65 if first else 0.0
    if kind == "Artifact Error" and first:
        confidence_score = 0.55
    explanation = "No failure was established from the available evidence."
    if first:
        explanation = f"Failure happened at Step {first['step']}. Evidence source: {first['evidence'].get('source', 'trace evidence')}"
        if kind == "Artifact Error" and first["step"] is None:
            explanation = "The final artifact failed validation, but the causal action is not localized."
    complete = not bundle.warnings and any(row.get("event") == "run_finished" and row.get("success") is True
                                          for row in bundle.events) and (artifact.get("status") == "PASS" or artifact.get("artifact_pass") is True)
    evidence = [first["evidence"]] if first else []
    diagnosis_id = hashlib.sha256(json.dumps({"run": bundle.run_id, "sources": bundle.source_hashes,
        "first": first}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return DiagnosisResult(diagnosis_id=diagnosis_id, run_id=bundle.run_id,
        failure=bool(first), step=first["step"] if first else None,
        first_failure_step=first["step"] if first else None, type=kind, failure_type=kind, layer=owner,
        confidence=first["confidence"] if first and owner else "unknown",
        confidence_score=confidence_score, explanation=explanation,
        responsibility={"primary": owner, "confidence": 0.95 if owner and first["confidence"] == "high" else 0.5 if owner else 0.0,
                        "alternatives": []}, evidence=evidence,
        v02_attribution={"failure_layer": boundary, "responsibility": owner, "evidence": evidence},
        suggested_repair=suggestions.get(kind), warnings=bundle.warnings,
        diagnosis_status="FAILURE_OBSERVED" if first else "NO_FAILURE_OBSERVED" if complete else "INSUFFICIENT_EVIDENCE")
