"""Evidence graph construction for auditable v0.3 diagnoses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


def _dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value or {})


def build_evidence_graph(bundle, diagnosis, feedback: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build a graph using only references present in a TraceBundle.

    Causal edges are emitted only when a failure and a final artifact failure
    are both observed. Otherwise the graph records temporal/evaluative links.
    """

    result = _dump(diagnosis)
    nodes = []
    edges = []
    seen = set()

    def node(node_id: str, node_type: str, label: str, evidence: Optional[Dict[str, Any]] = None):
        if node_id in seen:
            return
        seen.add(node_id)
        payload = {"id": node_id, "type": node_type, "label": label}
        if evidence:
            payload["evidence"] = evidence
        nodes.append(payload)

    def edge(source: str, target: str, relation: str):
        if source in seen and target in seen:
            candidate = {"from": source, "to": target, "relation": relation}
            if candidate not in edges:
                edges.append(candidate)

    task_id = getattr(bundle, "task_id", "") or getattr(bundle, "task", "") or getattr(bundle, "run_id", "")
    node("task_goal", "Task Goal", task_id, {"run_id": bundle.run_id, "requirements": getattr(bundle, "public_task", {})})
    previous_action = None
    for row in getattr(bundle, "steps", getattr(bundle, "trace", [])):
        step = row.get("step")
        if not isinstance(step, int):
            continue
        subgoal = (row.get("step_eval") or {}).get("subgoal_id")
        subgoal_id = f"subgoal_{subgoal}" if subgoal else f"subgoal_step_{step}"
        node(subgoal_id, "Subgoal", str(subgoal or f"step_{step}"), {"step": step})
        edge("task_goal", subgoal_id, "contains")
        action = row.get("action")
        action_id = f"action_{step}"
        if action:
            node(action_id, "Action", str(action.get("type", "unknown")), {"step": step, "action": action})
            edge(subgoal_id, action_id, "evaluates")
            if previous_action:
                edge(previous_action, action_id, "precedes")
            previous_action = action_id
        for label, observation in (("before", row.get("obs_before")), ("after", row.get("obs_after"))):
            if observation:
                state_id = f"state_{step}_{label}"
                node(state_id, "State", f"step {step} {label}", {
                    "step": step, "phase": label,
                    "screenshot": (observation or {}).get("screenshot_path"),
                    "ui_state": (observation or {}).get("ui_state", {}),
                })
                if action:
                    edge(state_id if label == "before" else action_id, action_id if label == "before" else state_id,
                         "observes_before" if label == "before" else "produces")
        if result.get("first_failure_step") == step or result.get("step") == step:
            failure_id = f"failure_{step}"
            node(failure_id, "Failure", str(result.get("failure_type") or result.get("type") or "Failure"), {
                "step": step, "confidence": result.get("confidence"), "evidence": result.get("evidence", []),
            })
            if action:
                edge(action_id, failure_id, "observed_failure")

    if getattr(bundle, "artifacts", []):
        artifact_id = "artifact_final"
        node(artifact_id, "Artifact", ", ".join(bundle.artifacts), {"result": getattr(bundle, "artifact_result", {})})
        if result.get("first_failure_step") is not None:
            relation = "causes" if result.get("artifact_status") == "FAIL" or result.get("success") is False and getattr(bundle, "artifact_result", {}).get("status") == "FAIL" or getattr(bundle, "artifact_result", {}).get("status") == "FAIL" else "evaluated_by"
            failure_id = f"failure_{result.get('first_failure_step')}"
            edge(failure_id, artifact_id, relation)
        else:
            edge("task_goal", artifact_id, "verified_by")

    # A read-only view of existing events; these edges express provenance,
    # not a new causal classifier or evaluator.
    for index, event in enumerate(getattr(bundle, "execution_events", [])):
        step = event.get("step")
        event_id = f"execution_{index}"
        node(event_id, "Execution Event", str(event.get("event", "execution")), event)
        edge("task_goal", event_id, "records")
        if type(step) is int:
            edge(f"action_{step}", event_id, "recorded_as")
            edge(event_id, f"state_{step}_after", "observed_after")
    node("diagnosis", "Diagnosis", str(result.get("failure_type") or "Diagnosis"), result)
    edge("task_goal", "diagnosis", "diagnosed_as")
    edge(f"failure_{result.get('first_failure_step')}", "diagnosis", "supports")
    edge("artifact_final", "diagnosis", "evaluated_evidence")
    for item in result.get("evidence", []):
        if not isinstance(item, dict):
            continue
        for index, event in enumerate(getattr(bundle, "execution_events", [])):
            if item.get("source") == event.get("_source") and item.get("line") == event.get("_line"):
                edge(f"execution_{index}", "diagnosis", "supports")
        for row in getattr(bundle, "steps", []):
            if item.get("source") == row.get("_source") and item.get("line") == row.get("_line"):
                edge(f"state_{row.get('step')}_after", "diagnosis", "supports")
        if type(item.get("step")) is int:
            edge(f"state_{item['step']}_after", "diagnosis", "supports")
    for row in getattr(bundle, "steps", []):
        if (row.get("action") or {}).get("type") == "export_file":
            edge(f"state_{row.get('step')}_after", "artifact_final", "exported_artifact")
    if feedback:
        node("human_feedback", "Human Feedback", "Human review", {"feedback_id": feedback.get("feedback_id"), "correct": feedback.get("diagnosis_correct", feedback.get("diagnosis", {}).get("correct"))})
        for candidate in (n["id"] for n in nodes if n["type"] == "Failure"):
            edge("human_feedback", candidate, "reviews")

    return {"schema_version": "0.3", "run_id": bundle.run_id, "nodes": nodes, "edges": edges}


def write_evidence_graph(run_dir, bundle, diagnosis, feedback: Optional[Mapping[str, Any]] = None) -> Path:
    root = Path(run_dir).resolve()
    target = root / "evidence_graph.json"
    if target.exists():
        raise FileExistsError(f"Evidence graph already exists: {target}")
    target.write_text(json.dumps(build_evidence_graph(bundle, diagnosis, feedback), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


__all__ = ["build_evidence_graph", "write_evidence_graph"]
