"""Explicit single repair attempt through the unchanged GIMP runtime/executor."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
import yaml
from diagagent.actions.parser import ActionParser
from diagagent.actions.schema import StopAction
from diagagent.agent.base import BaseAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.config import DesktopConfig
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.feedback.store import diagnosis_hash
from diagagent.repair.planner import validate_actions
from diagagent.trace.redaction import Redactor


ATTEMPT_POLICY = {
    "version": "0.2.1",
    "execution_mode": "explicit_single_attempt",
    "automatic_retry": False,
    "numbering_scope": "plan_id_from_policy_adoption",
    "retroactive_backfill": False,
    "environment_failure_rule": (
        "An environment failure remains a counted, failed attempt. A later run requires "
        "a new explicit invocation and receives a new attempt number."
    ),
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _write_new_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(Redactor().clean(payload), stream, ensure_ascii=False, indent=2)


def _register_attempt(parent_run, plan, plan_path, output_dir):
    """Allocate an immutable attempt number before any environment execution."""
    folder = parent_run / "repairs" / "attempts" / plan["plan_id"]
    folder.mkdir(parents=True, exist_ok=True)
    numbers = []
    for candidate in folder.glob("attempt_*.registration.json"):
        match = re.fullmatch(r"attempt_(\d+)\.registration\.json", candidate.name)
        if match:
            numbers.append(int(match.group(1)))
    number = max(numbers, default=0) + 1
    while True:
        attempt_id = uuid4().hex
        record = {
            "schema_version": "1.0",
            "attempt_id": attempt_id,
            "attempt_number": number,
            "plan_id": plan["plan_id"],
            "parent_run_id": parent_run.name,
            "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "registered_at": _utc_now(),
            "requested_output_root": str(Path(output_dir).resolve()),
            "status": "REGISTERED",
            "counts_toward_attempt_total": True,
            "attempt_policy": ATTEMPT_POLICY,
            "attempt_dir": str((folder / f"attempt_{number:04d}").resolve()),
        }
        registration = folder / f"attempt_{number:04d}.registration.json"
        try:
            _write_new_json(registration, record)
            return record, registration
        except FileExistsError:
            number += 1


def _attempt_status(report):
    if report.success:
        return "SUCCESS"
    owner = getattr(report.responsibility, "value", report.responsibility)
    if owner == "Environment" or report.failure_type in {
        "Environment Error", "Launch / Window Error", "Authentication Error", "Model API Error"
    }:
        return "ENVIRONMENT_FAILED"
    if owner == "Evaluator" or report.failure_type == "Evaluator Error":
        return "EVALUATOR_FAILED"
    return "REPAIR_FAILED"


def _save_attempt_outcome(registration_path, registration, payload):
    outcome = {
        **registration,
        **payload,
        "completed_at": _utc_now(),
        "counts_toward_attempt_total": True,
        "attempt_policy": ATTEMPT_POLICY,
    }
    target = registration_path.with_name(
        registration_path.name.replace(".registration.json", ".outcome.json")
    )
    _write_new_json(target, outcome)
    return outcome


def _attempt_outcome_exists(registration_path):
    return registration_path.with_name(
        registration_path.name.replace(".registration.json", ".outcome.json")
    ).exists()


class RepairAgent(BaseAgent):
    def __init__(self, actions):
        super().__init__(name="HumanDirectedRepair")
        self.actions = actions

    def reset(self, task_spec):
        self.iterator = iter(self.actions)

    def act(self, observation):
        action = next(self.iterator, None)
        return ActionParser.parse(action) if action is not None else StopAction()


def execute_plan(plan_file, output_dir, config=None):
    plan_path = Path(plan_file).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan["status"] != "READY" or not plan["actions"]:
        raise ValueError("Repair needs a concrete action plan")
    root = Path(plan["parent_run"])
    task_bytes = (root / "task_spec.yaml").read_bytes()
    if hashlib.sha256(task_bytes).hexdigest() != plan["task_sha256"] or diagnosis_hash(root) != plan["diagnosis_sha256"]:
        raise ValueError("Parent task or diagnosis changed after planning")
    if hashlib.sha256(Path(plan["feedback_file"]).read_bytes()).hexdigest() != plan["feedback_sha256"]:
        raise ValueError("Human feedback changed after planning")
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    backend = metadata["backend"]
    if backend not in {"mock", "real_gimp"}:
        raise ValueError("Unknown parent backend")
    if backend == "real_gimp" and plan["feedback_source"] != "human":
        raise ValueError("Real repair requires explicit human feedback")
    task = yaml.safe_load(task_bytes)
    validate_actions(plan["actions"], task)
    asset = json.loads((root / "asset.json").read_text(encoding="utf-8"))
    if hashlib.sha256(Path(task["initial_state"]["input_file"]).read_bytes()).hexdigest() != asset["sha256"]:
        raise ValueError("Input asset changed after the parent run")
    config = config or DesktopConfig(**metadata.get("config", {}))
    registration, registration_path = _register_attempt(root, plan, plan_path, output_dir)
    env = None
    try:
        env = GIMPEnvironment(backend=backend, run_root=output_dir, config=config)
        env.agent_type = "human_directed_repair"
        env.prepare(task)
        lineage = {
            "parent_run_id": root.name,
            "parent_run": str(root),
            "plan_id": plan["plan_id"],
            "plan_sha256": registration["plan_sha256"],
            "feedback_sha256": plan["feedback_sha256"],
            "feedback_source": plan["feedback_source"],
            "attempt_id": registration["attempt_id"],
            "attempt_number": registration["attempt_number"],
            "attempt_registration": str(registration_path),
            "attempt_policy": ATTEMPT_POLICY,
        }
        env.trace_recorder.save_json("repair_attempt.json", registration)
        env.trace_recorder.save_json("repair_lineage.json", lineage)
        report = AgentRuntime(env, automatic_recovery=False).run_task(
            task, agent=RepairAgent(plan["actions"])
        )
        outcome = _save_attempt_outcome(registration_path, registration, {
            "status": _attempt_status(report),
            "run_id": env.trace_recorder.run_id,
            "run_dir": str(env.trace_recorder.run_dir),
            "success": report.success,
            "task_status": report.task_status,
            "failure_type": report.failure_type,
            "responsibility": getattr(report.responsibility, "value", report.responsibility),
            "backend": backend,
            "evidence_kind": "real_gui" if backend == "real_gimp" else "mock",
        })
        audit_dir = Path(registration["attempt_dir"])
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "before").mkdir(exist_ok=True)
        (audit_dir / "after").mkdir(exist_ok=True)
        (audit_dir / "action.json").write_text(json.dumps({
            "target_step": plan.get("target_step"), "action": plan.get("action"),
            "parameters": plan.get("parameters", {}),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (audit_dir / "before" / "state.json").write_text(json.dumps({
            "run_id": root.name, "diagnosis_sha256": plan["diagnosis_sha256"],
            "success": False, "source": "parent_run.result.json",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (audit_dir / "after" / "state.json").write_text(json.dumps({
            "run_id": env.trace_recorder.run_id, "success": report.success,
            "task_status": report.task_status, "source": "repair_run.result.json",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (audit_dir / "result.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
        env.trace_recorder.save_json("repair_outcome.json", {**lineage, **outcome})
        from diagagent.repair.replay import verify_repair
        verify_repair(root, env.trace_recorder.run_dir)
        return env.trace_recorder.run_dir, report
    except Exception as error:
        if not _attempt_outcome_exists(registration_path):
            _save_attempt_outcome(registration_path, registration, {
                "status": "SETUP_FAILED",
                "run_id": env.trace_recorder.run_id if env and env.trace_recorder else None,
                "run_dir": str(env.trace_recorder.run_dir) if env and env.trace_recorder else None,
                "success": False,
                "task_status": "UNKNOWN",
                "failure_type": getattr(error, "error_type", type(error).__name__),
                "responsibility": "Environment",
                "backend": backend,
                "evidence_kind": "real_gui" if backend == "real_gimp" else "mock",
            })
        raise
