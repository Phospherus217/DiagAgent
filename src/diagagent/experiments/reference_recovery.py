"""Reference recovery probes for the v1.0 real-GIMP admission gate.

This module is deliberately separate from agent healing.  It accepts a known
correct repair policy, lowers it through the existing action stack, and can
perform one explicitly requested real-GIMP execution.  The default is a
non-executing structural probe; it never retries or falls back to mock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

import yaml
from PIL import Image, ImageChops

from diagagent.actions.parser import ActionParser
from diagagent.actions.validator import ActionValidator
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.healing.real_executor import RealGIMPRecoveryExecutor
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.lowering.lowering_engine import LoweringEngine
from diagagent.lowering.structural_lowering import StructuralLowerer
from diagagent.schemas.recovery import HealingState, RecoverySession, VerificationContract
from diagagent.tasks import load_task


REPAIR_TYPES = {
    "Parameter Error": "parameter_update",
    "File I/O Error": "file_io_recovery",
    "Tool Selection Error": "tool_reselect",
    "Canvas Target Error": "target_recompute",
    "Dialog Operation Error": "dialog_recovery",
    "Dialog Error": "dialog_recovery",
}


class _StructuralProbeBackend:
    """Geometry-only stand-in used to validate structural lowering offline."""
    window = object()

    def activate(self, _window):
        return None

    def find_dialog(self, _name):
        return type("Dialog", (), {"left": 100, "top": 100, "width": 500,
                                    "bottom": 500, "right": 600})()


def reference_action(case: Mapping[str, Any]) -> Dict[str, Any]:
    failure = str(case.get("failure_type", ""))
    explicit = case.get("reference_policy") or {}
    if isinstance(explicit, Mapping) and isinstance(explicit.get("repair_action"), Mapping):
        return dict(explicit["repair_action"])
    if failure == "Tool Selection Error":
        return {"type": "select_tool", "tool_name": case.get("expected_tool", "Crop Tool")}
    if failure == "Canvas Target Error":
        return {"type": "crop_canvas", "region": case.get("region", "center"),
                "coordinate": case.get("coordinate", "image")}
    if failure in {"Dialog Error", "Dialog Operation Error"}:
        if str(case.get("dialog", "Scale Image")) == "Export Image":
            return {"type": "export_file", "path": case.get("output_name", "output.png"), "format": "png"}
        return {"type": "resize_image", "width": int(case.get("width", 512)),
                "height": int(case.get("height", 512))}
    if failure == "Parameter Error":
        return {"type": "resize_image", "width": int(case.get("width", 512)),
                "height": int(case.get("height", 512))}
    if failure == "File I/O Error":
        return {"type": "export_file", "path": case.get("output_name", "output.png"), "format": "png"}
    raise ValueError(f"No reference recovery action for {failure}")


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _compile_action(case: Mapping[str, Any], action: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    parsed = ActionParser.parse(action)
    allowed = case.get("allowed_actions")
    valid, error = ActionValidator.validate(parsed, allowed)
    if not valid:
        raise ValueError(error)
    ui = {"canvas_bbox": tuple(case.get("canvas_bbox", (220, 115, 996, 662))),
          "image_size": list(case.get("image_size", (1024, 768))),
          "layout_scope": "fixed"}
    lowered = LoweringEngine().lower(parsed, ui_primitives=ui)
    if not lowered:
        raise ValueError("reference recovery lowered to an empty action list")
    structural = StructuralLowerer(_StructuralProbeBackend())
    atomic = []
    for item in lowered:
        atomic.extend(structural.lower(item))
    return [item.model_dump() for item in lowered], [item.model_dump() for item in atomic]


def _compile_policy_actions(case: Mapping[str, Any], primary: Dict[str, Any]):
    semantic, atomic = [], []
    for prefix in case.get("reference_prefix", []) or []:
        prefix_semantic, prefix_atomic = _compile_action(case, dict(prefix))
        semantic.extend(prefix_semantic); atomic.extend(prefix_atomic)
    primary_semantic, primary_atomic = _compile_action(case, primary)
    semantic.extend(primary_semantic); atomic.extend(primary_atomic)
    for suffix in case.get("reference_suffix", []) or []:
        suffix_semantic, suffix_atomic = _compile_action(case, dict(suffix))
        semantic.extend(suffix_semantic); atomic.extend(suffix_atomic)
    return semantic, atomic


def _persist_recovery_audit(root: Path, case: Mapping[str, Any], result: Dict[str, Any],
                            action: Dict[str, Any], execution: Mapping[str, Any],
                            final: Mapping[str, Any]) -> None:
    """Persist the complete reference-case audit package.

    Reference runs use a frozen, known policy rather than an agent diagnosis.
    The distinction is recorded explicitly in ``diagnosis.json`` so these
    packages can be admitted as backend evidence without being confused with
    model or failure-injection results.
    """
    run_dir = Path(result["run_dir"])
    summary = final.get("summary") or {}
    artifact = final.get("artifact_evaluation") or {}
    failure_type = str(case.get("failure_type", ""))
    step = case.get("first_failure_step")
    _write(root / "case.json", dict(case))
    diagnosis = {
        "schema_version": "1.0",
        "diagnosis_id": f"reference_{case.get('case_id', 'case')}",
        "run_id": summary.get("run_id", run_dir.name),
        "failure": False,
        "observed_failure": False,
        "evidence_kind": "reference_policy",
        "failure_type": failure_type,
        "first_failure_step": step,
        "step": step,
        "type": failure_type,
        "confidence": "none",
        "confidence_score": 0.0,
        "diagnosis_status": "REFERENCE_ONLY_NO_OBSERVED_FAILURE",
        "explanation": "Known failure category supplied by the frozen real-GIMP case manifest; this reference replay does not fabricate a failed run.",
        "evidence": [
            {"source": "case.json", "field": "failure_type", "value": failure_type},
            {"source": "case.json", "field": "first_failure_step", "value": step},
            {"source": "trace.jsonl", "field": "reference_replay", "value": "single explicit recovery attempt"},
        ],
    }
    _write(root / "diagnosis.json", diagnosis)

    graph = {
        "schema_version": "1.0",
        "run_id": summary.get("run_id", run_dir.name),
        "evidence_kind": "reference_policy",
        "nodes": [
            {"id": "reference_case", "type": "Reference Case", "label": str(case.get("case_id"))},
            {"id": "reference_policy", "type": "Policy", "label": failure_type,
             "evidence": {"source": "case.json", "observed_failure": False}},
            {"id": "repair_action", "type": "Action", "label": action.get("type", "unknown"),
             "evidence": {"action": action, "attempts": 1}},
            {"id": "artifact_final", "type": "Artifact", "label": "artifact evaluation",
             "evidence": artifact},
        ],
        "edges": [
            {"from": "reference_case", "to": "reference_policy", "relation": "supplies"},
            {"from": "reference_policy", "to": "repair_action", "relation": "lowers"},
            {"from": "repair_action", "to": "artifact_final", "relation": "verified_by"},
        ],
    }
    _write(root / "evidence_graph.json", graph)
    repair_plan = {
        "schema_version": "1.0",
        "plan_id": f"reference_plan_{case.get('case_id', 'case')}",
        "repairability": "REPAIRABLE",
        "repair_type": REPAIR_TYPES.get(failure_type, "unsupported"),
        "repair_action": action,
        "target_step": step,
        "restart_mode": "fresh_task_replay",
        "evidence_node_ids": ["reference_policy", "repair_action"],
        "attempt_budget": 1,
        "retry_used": False,
        "reference_policy": True,
    }
    _write(root / "repair_plan.json", repair_plan)

    contract = VerificationContract(required_checks=["process", "artifact", "task"])
    attempt = {
        "process_recovered": bool(result.get("verification_success")),
        "artifact_recovered": bool(result.get("artifact_pass")),
        "task_success": bool(summary.get("success")),
        "evidence_refs": [
            str(run_dir / "trace.jsonl"), str(run_dir / "events.jsonl"),
            str(run_dir / "screenshots"), str(run_dir / "artifacts"),
        ],
        "violated_side_effects": [],
    }
    session = RecoverySession(
        run_id=summary.get("run_id", run_dir.name),
        task_id=summary.get("task_id", case.get("task_file", "")),
        trigger_step=step,
        trigger_reason=failure_type,
        diagnosis_id=diagnosis["diagnosis_id"],
        failure_type=failure_type,
        first_failure_step=step,
        diagnosis_confidence=1.0,
        repairability="REPAIRABLE",
        repairability_reason="Frozen reference policy and one-attempt budget.",
        repair_plan_id=repair_plan["plan_id"],
        repair_attempt_ids=[f"attempt_{case.get('case_id', 'case')}_001"],
        requires_human_approval=False,
        approved_by_human=False,
    )
    verification = RecoveryVerifier().verify(session=session, attempt=attempt, contract=contract)
    _write(root / "verification.json", verification.model_dump())
    session.recovery_success = verification.recovered
    session.recovery_reason = verification.reason
    session.state = HealingState.RECOVERED if verification.recovered else HealingState.NOT_RECOVERED
    _write(root / "recovery_session.json", session.model_dump())
    _write(root / "replay_result.json", {
        "schema_version": "1.0", "attempt_id": run_dir.name,
        "recovered": verification.recovered, "retry_used": False,
        "process_recovered": verification.process_recovered,
        "artifact_recovered": verification.artifact_recovered,
        "task_recovered": verification.task_recovered,
        "evidence_refs": verification.evidence_refs,
        "verification_source": "independent_recovery_verifier",
    })


def _center_crop_matches(case: Mapping[str, Any], run_dir: Path) -> bool:
    """Independently verify the spatial target for the center-crop case.

    The task contract defines the center crop as the central 50% of the source
    image.  Verification therefore checks the exact expected dimensions and
    compares the artifact with that immutable source region.  A permissive
    template match would allow an incorrectly targeted crop to pass.
    """
    task_file = case.get("task_file")
    if not task_file:
        return False
    try:
        task = load_task(Path(task_file).resolve())
        source = Path(task["initial_state"]["input_file"])
        output = run_dir / "artifacts" / str(task["initial_state"].get("output_file", "output.png"))
        with Image.open(source).convert("RGB") as image, Image.open(output).convert("RGB") as actual:
            crop_w, crop_h = round(image.width * 0.5), round(image.height * 0.5)
            left = (image.width - crop_w) // 2
            top = (image.height - crop_h) // 2
            if (actual.width, actual.height) != (crop_w, crop_h):
                return False
            expected = image.crop((left, top, left + crop_w, top + crop_h))
            diff = ImageChops.difference(expected, actual)
            extrema = diff.getextrema()
            mean_error = sum((lo + hi) / 2.0 for lo, hi in extrema) / len(extrema)
            return mean_error <= 1.0
    except (OSError, KeyError, ValueError):
        return False


def run_reference_case(case: Mapping[str, Any], output_root: Union[str, Path], *, execute: bool = False) -> Path:
    """Persist one reference probe result; execute at most one real attempt."""
    case_id = str(case.get("case_id", "reference_case"))
    root = Path(output_root).resolve() / case_id
    root.mkdir(parents=True, exist_ok=True)
    if (root / "reference_probe.json").exists():
        raise FileExistsError(
            f"Reference result already exists for {case_id}; use a new output root instead of overwriting an attempt"
        )
    action = reference_action(case)
    repair_type = REPAIR_TYPES.get(str(case.get("failure_type")), "unsupported")
    result: Dict[str, Any] = {
        "schema_version": "1.0", "case_id": case_id,
        "failure_type": case.get("failure_type"), "repair_type": repair_type,
        "reference_policy": {"repair_type": repair_type, "repair_action": action,
                              "correct_diagnosis": True, "llm_used": False},
        "lowering_success": False, "execution_success": False,
        "verification_success": False, "artifact_pass": False,
        "retry_used": False, "run_dir": None, "evidence_refs": [],
        "status": "DRY_RUN" if not execute else "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(root / "reference_policy.json", result["reference_policy"])
    try:
        lowered, atomic = _compile_policy_actions(case, action)
        result["lowering_success"] = True
        result["evidence_refs"] = ["reference_policy.json", "lowered_actions.json"]
        _write(root / "lowered_actions.json", {"actions": lowered, "atomic_actions": atomic,
                                                "layout_scope": "fixed"})
    except Exception as error:
        result.update(status="LOWERING_MISSING", reason=str(error))
        _write(root / "reference_probe.json", result)
        return root / "reference_probe.json"
    if not execute:
        result["reason"] = "Structural reference probe only; real GIMP execution was not requested."
        _write(root / "reference_probe.json", result)
        return root / "reference_probe.json"
    if str(case.get("backend", "real_gimp")) != "real_gimp":
        result.update(status="BACKEND_NOT_QUALIFIED", reason="Reference execution requires backend=real_gimp.")
        _write(root / "reference_probe.json", result)
        return root / "reference_probe.json"
    task_file = case.get("task_file")
    if not task_file:
        result.update(status="EVIDENCE_INSUFFICIENT", reason="task_file is required for explicit execution.")
        _write(root / "reference_probe.json", result)
        return root / "reference_probe.json"
    env = None
    try:
        task_path = Path(task_file).resolve()
        task = load_task(task_path)
        env = GIMPEnvironment(backend="real_gimp", run_root=root)
        env.agent_type = "reference_recovery_probe"
        env.reset(task)
        recovery_executor = RealGIMPRecoveryExecutor(env)
        execution_results = []
        effects = []
        for raw_action in [*(case.get("reference_prefix", []) or []), action,
                           *(case.get("reference_suffix", []) or [])]:
            attempt = recovery_executor.execute(raw_action, finalize=False)
            execution_results.append(attempt)
            effects.append(attempt.get("effect_evidence") or {})
        # Reference replays terminate explicitly so the task evaluator sees
        # the same completion boundary as a normal agent run.  This is a
        # control action, not a retry or a second repair attempt.
        if execution_results and all(bool(item.get("success")) for item in execution_results):
            env.execute(ActionParser.parse({"type": "stop"}))
        execution = execution_results[0] if execution_results else {}
        effect = effects[0] if effects else {}
        result["execution_success"] = bool(execution_results) and all(bool(item.get("success")) for item in execution_results)
        result["run_dir"] = str(env.trace_recorder.run_dir)
        result["evidence_refs"] = ["reference_policy.json", "lowered_actions.json",
                                    "trace.jsonl", "events.jsonl", "screenshots/"]
        if str(case.get("failure_type")) in {"Dialog Error", "Dialog Operation Error", "Parameter Error", "File I/O Error"}:
            result["verification_success"] = bool(execution.get("success")) and effect.get("status") == "PASS"
        elif str(case.get("failure_type")) == "Tool Selection Error":
            # The profile has no active-tool API.  A functional downstream
            # click/type/export probe is therefore required and recorded as an
            # indirect signal; the raw proxy limitation remains explicit.
            downstream = execution_results[1:] if len(execution_results) > 1 else []
            result["verification_success"] = bool(downstream) and all(bool(item.get("success")) for item in downstream)
            result["verification_note"] = "active tool signal is unavailable; downstream action and artifact provide an indirect functional signal"
        elif str(case.get("failure_type")) == "Canvas Target Error":
            result["verification_success"] = _center_crop_matches(case, Path(result["run_dir"]))
            result["verification_note"] = "target location independently verified by exact center-crop artifact comparison"
        final = {}
        try:
            final = env.evaluate_final()
            artifact = final.get("artifact_evaluation") or {}
            result["artifact_pass"] = bool(artifact.get("artifact_pass"))
        except Exception as error:
            result["artifact_error"] = str(error)
        # A reference replay proves that the known repair path works.  It is
        # admitted only when the independent task evaluator also reports
        # success; executor success alone is never promoted to recovery.
        task_pass = bool((final.get("summary") or {}).get("success"))
        result["task_pass"] = task_pass
        result["status"] = "READY" if (
            result["execution_success"] and result["verification_success"]
            and result["artifact_pass"] and task_pass
        ) else "NOT_QUALIFIED"
        # Keep the reference evidence package beside the immutable GUI run.
        # This is written before the environment is closed so all paths and
        # independent verification signals are available.
        if result.get("run_dir"):
            _persist_recovery_audit(root, case, result, action, execution, final)
    except Exception as error:
        result.update(status="BACKEND_NOT_QUALIFIED", reason=str(error))
    finally:
        if env is not None:
            env.close()
    _write(root / "reference_probe.json", result)
    return root / "reference_probe.json"


def run_reference_manifest(manifest: Union[str, Path], output_root: Union[str, Path], *, execute: bool = False,
                           case_id: Optional[str] = None) -> List[Path]:
    manifest_path = Path(manifest).resolve()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("Reference manifest must contain a cases list")
    selected = [case for case in cases if case_id is None or case.get("case_id") == case_id]
    if case_id and not selected:
        raise ValueError(f"Unknown reference case: {case_id}")
    return [run_reference_case(case, output_root, execute=execute) for case in selected]


__all__ = ["reference_action", "run_reference_case", "run_reference_manifest"]
