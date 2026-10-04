"""Bounded repair using human instructions and public execution actions, never gold."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
import yaml
from diagagent.actions.parser import ActionParser
from diagagent.actions.validator import ActionValidator
from diagagent.diagnosis.evidence import load_evidence
from diagagent.feedback.store import HumanFeedback, diagnosis_hash
from diagagent.tasks import output_name
from diagagent.trace.redaction import Redactor


def validate_actions(actions, task):
    if not isinstance(actions, list) or not actions:
        raise ValueError("Repair requires nonempty actions")
    if len(actions) > task.get("max_steps", 20):
        raise ValueError("Repair exceeds the original decision budget")
    for action in actions:
        parsed = ActionParser.parse(action)
        valid, error = ActionValidator.validate(parsed, task.get("allowed_actions"))
        if not valid:
            raise ValueError(error)
        if parsed.type == "export_file" and output_name(parsed.path) != task["initial_state"]["output_file"]:
            raise ValueError("Repair export must use the original output handle")


def create_plan(run_dir, feedback_file):
    root, source = Path(run_dir).resolve(), Path(feedback_file).resolve()
    record = json.loads(source.read_text(encoding="utf-8"))
    feedback = HumanFeedback.model_validate({k: v for k, v in record.items() if k in HumanFeedback.model_fields})
    if feedback.run_id != root.name or feedback.diagnosis_sha256 != diagnosis_hash(root):
        raise ValueError("Stale feedback or wrong run")
    task_bytes = (root / "task_spec.yaml").read_bytes()
    task = yaml.safe_load(task_bytes)
    trace, events, diagnosis, warnings = load_evidence(root)
    if warnings:
        raise ValueError("Cannot repair incomplete evidence: " + "; ".join(warnings))
    if not diagnosis.get("failure", diagnosis.get("failure_type")):
        raise ValueError("No diagnosed failure to repair")
    kind = feedback.corrected_failure_type or diagnosis.get("type") or diagnosis.get("failure_type")
    step = feedback.corrected_step if feedback.corrected_step is not None else diagnosis.get("step", diagnosis.get("first_failure_step"))
    instruction = feedback.repair_instruction
    replacement = None
    try:
        raw = json.loads(instruction)
        if isinstance(raw, dict):
            replacement = ActionParser.parse(raw).to_dict()
    except (ValueError, TypeError):
        pass
    if replacement is None:
        dimensions = re.search(r"(?<!\d)(\d+)\s*[x×X]\s*(\d+)(?!\d)", instruction)
        if dimensions and kind in {"Parameter Error", "Execution No-op", "Action Format Error"}:
            replacement = {"type": "resize_image", "width": int(dimensions[1]), "height": int(dimensions[2])}
        elif kind == "Action Format Error" and re.search(r"export|导出|path|路径", instruction, re.I):
            replacement = {"type": "export_file", "path": output_name(task["initial_state"]["output_file"]), "format": "png"}
    actions = []
    if replacement is not None and isinstance(step, int):
        received = {r["step"]: r["raw_action"] for r in events if r.get("event") == "action_received"}
        received.update({r["step"]: r["action"] for r in trace if r.get("action")})
        for index in sorted(received):
            if index < step and received[index].get("type") != "stop":
                actions.append(received[index])
        actions.append(replacement)
        for index in sorted(received):
            if index > step and received[index].get("type") != "stop":
                actions.append(received[index])
        if actions[-1].get("type") != "export_file" and "export_file" in task.get("allowed_actions", []):
            actions.append({"type": "export_file", "path": output_name(task["initial_state"]["output_file"]), "format": "png"})
        validate_actions(actions, task)
    plan = {"plan_id": uuid4().hex, "parent_run": str(root), "parent_run_id": root.name,
            "diagnosis_sha256": diagnosis_hash(root), "task_sha256": hashlib.sha256(task_bytes).hexdigest(),
            "feedback_file": str(source), "feedback_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "feedback_source": feedback.source, "failure_type": kind,
            "repair_instruction": instruction, "status": "READY" if actions else "NEEDS_REPLAN",
            "strategy": "Reset original task; replay prefix, corrected action and suffix; evaluate unchanged contract."
                if actions else "Provide a structured replacement action or resolve the environment cause before re-planning.",
            "actions": actions, "automatic_retry": False}
    plan.update({
        "target_step": step if isinstance(step, int) else None,
        "action": replacement,
        "parameters": {key: value for key, value in (replacement or {}).items() if key != "type"},
        "verification": {"evaluator": "original_task_contract", "success_required": True},
    })
    folder = root / "repairs"
    folder.mkdir(exist_ok=True)
    target = folder / f"{plan['plan_id']}.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(Redactor().clean(plan), stream, ensure_ascii=False, indent=2)
    return target
