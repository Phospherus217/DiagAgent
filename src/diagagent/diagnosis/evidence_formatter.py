"""Serialize only public run evidence for hybrid diagnosis."""

import json
from typing import Any, Dict


def format_evidence(bundle, diagnosis=None, graph=None) -> Dict[str, Any]:
    """Return a bounded, JSON-safe evidence view.

    Task gold, hidden evaluator state, credentials and raw model payloads are
    intentionally excluded. The formatter is the only input boundary for the
    optional language-model reasoner.
    """

    result = diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else (diagnosis or {})
    steps = []
    for row in getattr(bundle, "steps", getattr(bundle, "trace", [])):
        steps.append({
            "step": row.get("step"),
            "phase": row.get("phase"),
            "action": row.get("action"),
            "execution_result": row.get("execution_result"),
            "step_eval": row.get("step_eval"),
            "error_type": row.get("error_type"),
            "obs_before": {"screenshot_path": (row.get("obs_before") or {}).get("screenshot_path")},
            "obs_after": {"screenshot_path": (row.get("obs_after") or {}).get("screenshot_path")},
        })
    payload = {
        "run_id": getattr(bundle, "run_id", ""),
        "task_id": getattr(bundle, "task_id", "") or getattr(bundle, "task", ""),
        "steps": steps,
        "screenshots": list(getattr(bundle, "screenshots", [])),
        "artifacts": list(getattr(bundle, "artifacts", [])),
        "artifact_result": getattr(bundle, "artifact_result", {}),
        "diagnosis": {
            "failure": result.get("failure"),
            "failure_type": result.get("failure_type") or result.get("type"),
            "first_failure_step": result.get("first_failure_step") or result.get("step"),
            "confidence": result.get("confidence"),
            "evidence": result.get("evidence", []),
        },
    }
    if graph:
        graph_value = graph.model_dump() if hasattr(graph, "model_dump") else graph
        payload["evidence_graph"] = {"nodes": graph_value.get("nodes", []), "edges": graph_value.get("edges", [])}
    return payload


def format_evidence_json(bundle, diagnosis=None, graph=None) -> str:
    """Stable JSON representation suitable for a model user message."""
    return json.dumps(format_evidence(bundle, diagnosis, graph), ensure_ascii=False, sort_keys=True)


__all__ = ["format_evidence", "format_evidence_json"]
