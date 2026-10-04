"""Prospectively registered real-GIMP comparison with one common failed original.

B1/B2 each get one fresh task replay. The parameter mutation is a persistent
oracle action error. GUI cancel and file locking are one-time disturbances:
both recovery arms get clean workspaces. No raised synthetic error is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from diagagent.actions.parser import ActionParser
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.config import DesktopConfig
from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.first_failure import step_status
from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import load_trace
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.healing.loop import HealingLoop
from diagagent.healing.policy import RecoveryPolicy
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.recovery import RecoverySession, VerificationContract
from diagagent.tasks import load_task

VERSION = "portfolio-matched-v2"
ROOT = Path(__file__).resolve().parents[3]
TASK = ROOT / "benchmark/tasks/resize_image.yaml"
FAMILIES = {
    "parameter": {"failure_type": "Parameter Error", "step": 2,
                  "fault": "persistent oracle resize 512x512 -> 256x256"},
    "dialog": {"failure_type": "Dialog Operation Error", "step": 2,
               "fault": "one-time real Escape at Scale confirmation; clean replay for BOTH B1/B2"},
    "fileio": {"failure_type": "File I/O Error", "step": 3,
               "fault": "one-time exclusive OS file lock at PNG confirmation; clean replay for BOTH B1/B2"},
}
SIDE_EFFECTS = ["unexpected_crop", "unexpected_resize", "output_path_escape"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical(action):
    return ActionParser.parse(action).to_dict()


def contract():
    return VerificationContract(
        required_checks=["process", "artifact", "task", "policy_action_executed", "source_unchanged"],
        forbidden_side_effects=SIDE_EFFECTS)


class ReplayPolicy(RecoveryPolicy):
    def decide(self, policy_input):
        decision = super().decide(policy_input)
        return decision.model_copy(update={"restart_mode": "fresh_task_replay",
            "verification_contract": contract()})


class SequenceAgent(RuleAgent):
    """Explicit oracle scaffold; guarded action replaces its target slot."""
    def __init__(self, *, wrong_parameter=False, repair_action=None):
        super().__init__(name="registered_oracle_scaffold")
        self.wrong_parameter = wrong_parameter
        self.repair_action = repair_action
        self.changed = False

    def reset(self, task_spec):
        super().reset(task_spec)
        self.changed = False

    def act(self, observation):
        action = super().act(observation)
        if not self.changed and self.repair_action and action.type == self.repair_action["type"]:
            self.changed = True
            return ActionParser.parse(self.repair_action)
        if self.wrong_parameter and action.type == "resize_image":
            return action.model_copy(update={"width": 256, "height": 256})
        return action


class FaultEnvironment(GIMPEnvironment):
    """Inject actual UI/OS disturbances, never labelled Python exceptions."""
    def __init__(self, *, fault=None, **kwargs):
        super().__init__(**kwargs)
        self.fault = fault
        self.injected = False
        self.lock_handle = None

    def _execute_primitive(self, action):
        raw = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        if not self.injected and self.fault == "dialog" and raw.get("type") == "click":
            dialogs = [w for w in self.backend.windows() if "Scale Image" in w.title]
            if dialogs:
                window = dialogs[0]
                point = (window.left + round(window.width * .64), window.bottom - 22)
                if (raw.get("x"), raw.get("y")) == point:
                    self._event("fault_injection_started", kind="actual_gui_cancel", replaced_action=raw)
                    result = super()._execute_primitive({"type": "hotkey", "keys": "esc"})
                    self.injected = True
                    self._event("fault_injection_reached", kind="actual_gui_cancel",
                                observed_size=self.backend.current_image_size(), dispatch_success=result.success)
                    return result
        if (not self.injected and self.fault == "fileio" and raw.get("type") == "hotkey"
                and raw.get("keys") in ("enter", ["enter"])
                and self.backend.active_dialog == "Export Image as PNG"):
            import _winapi
            path = self.trace_recorder.artifacts_dir / "output.png"
            # CREATE_NEW and share mode zero: a real filesystem conflict at
            # final GUI save, confined to this experiment-owned output.
            self.lock_handle = _winapi.CreateFile(str(path), 0x40000000, 0, 0, 1, 0x80, 0)
            self.injected = True
            self._event("fault_injection_reached", kind="exclusive_os_file_lock",
                        path="artifacts/output.png", share_mode=0, transient=True)
        return super()._execute_primitive(action)

    def close(self):
        try:
            super().close()
        finally:
            if self.lock_handle is not None:
                import _winapi
                _winapi.CloseHandle(self.lock_handle)
                self.lock_handle = None


def inspect_run(run_dir, task, source_hash, repair_action=None):
    """Derive checks and counts from persisted observations, never dispatch alone."""
    run_dir = Path(run_dir)
    result, evaluation = read(run_dir / "result.json"), read(run_dir / "evaluation.json")
    trace, events = jsonl(run_dir / "trace.jsonl"), jsonl(run_dir / "events.jsonl")
    actions = [r["action"] for r in trace if r.get("action") and r["action"].get("type") != "stop"]
    matches = sum(canonical(a) == canonical(repair_action) for a in actions) if repair_action else None
    path = run_dir / "artifacts/output.png"
    measurements = {"reference_rgb_mae": None, "mae_threshold": 8.0, "output_size": None,
                    "method": "full-image Pillow Lanczos comparison; scoped crop heuristic"}
    crop_ok = size_ok = None
    if path.is_file() and path.stat().st_size:
        try:
            with Image.open(path) as exported, Image.open(task["initial_state"]["input_file"]) as original:
                exported.load()
                reference = original.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS)
                measurements["output_size"] = list(exported.size)
                size_ok = exported.size == (512, 512)
                if size_ok:
                    diff = ImageChops.difference(exported.convert("RGB"), reference)
                    measurements["reference_rgb_mae"] = sum(ImageStat.Stat(diff).mean) / 3
                    crop_ok = measurements["reference_rgb_mae"] <= measurements["mae_threshold"]
        except (OSError, ValueError):
            pass
    exports = [r["action"] for r in events if r.get("event") == "primitive_started"
               and (r.get("action") or {}).get("type") == "type"
               and str((r.get("action") or {}).get("text", "")).lower().endswith(".png")]
    path_ok = bool(exports) and all(Path(a["text"]).resolve() == path.resolve() for a in exports)
    steps = evaluation.get("steps", [])
    required = {s["id"] for s in task["subgoals"]}
    seen = {s.get("subgoal_id") for s in steps if step_status(s) == "PASS"}
    process = bool(steps) and required.issubset(seen) and all(step_status(s) == "PASS" for s in steps)
    return {"process_recovered": process,
        "artifact_recovered": (evaluation.get("artifact") or {}).get("status") == "PASS",
        "task_recovered": result.get("success") is True,
        "checks": {"policy_action_executed": matches == 1 if repair_action else True,
                   "source_unchanged": sha(task["initial_state"]["input_file"]) == source_hash},
        "side_effect_checks": dict(zip(SIDE_EFFECTS, [crop_ok, size_ok, path_ok])),
        "measurements": measurements, "repair_action": repair_action, "policy_action_match_count": matches,
        "extra_semantic_actions": len(actions) + sum(r.get("phase") == "reset" for r in trace),
        "extra_primitive_actions": sum(r.get("event") == "primitive_started" for r in events),
        "run_duration_seconds": result.get("duration_seconds"),
        "evidence_refs": ["trace.jsonl", "events.jsonl", "evaluation.json", "result.json", "artifacts/output.png"]}


def execute_condition(task, output, source_hash, *, wrong_parameter=False, fault=None, repair_action=None):
    env = FaultEnvironment(backend="real_gimp", run_root=output, fault=fault)
    started = time.monotonic()
    agent = SequenceAgent(wrong_parameter=wrong_parameter, repair_action=repair_action)
    report = AgentRuntime(env, automatic_recovery=False).run_task(task, agent)
    run_dir = env.trace_recorder.run_dir
    measurement = inspect_run(run_dir, task, source_hash, repair_action)
    measurement.update(run_dir=str(run_dir), recovery_time_seconds=time.monotonic() - started)
    dump(run_dir / "protocol_measurements.json", measurement)
    return {"run_dir": str(run_dir), "report": report.model_dump(), "measurements": measurement}


def initial_failure_observed(family, condition):
    """Admission does not depend on diagnosis prediction or recovery success."""
    root = Path(condition["run_dir"])
    trace, events = jsonl(root / "trace.jsonl"), jsonl(root / "events.jsonl")
    if family == "parameter":
        return any((r.get("action") or {}).get("width") == 256
                   and (r.get("obs_after") or {}).get("ui_state", {}).get("image_size") == [256, 256]
                   for r in trace)
    if family == "dialog":
        reached = any(r.get("event") == "fault_injection_reached" and r.get("kind") == "actual_gui_cancel" for r in events)
        no_change = any(r.get("event") == "run_error" and "resized image dimensions" in r.get("error_message", "") for r in events)
        return reached and no_change
    reached = any(r.get("event") == "fault_injection_reached" and r.get("kind") == "exclusive_os_file_lock" for r in events)
    failed_save = any(r.get("event") == "run_error" and "GUI exported decodable artifact" in r.get("error_message", "") for r in events)
    return reached and failed_save


def one_case(family, output, task, registration):
    root = output / family
    root.mkdir()
    source_hash = registration["input_sha256"]
    b0 = execute_condition(task, root / "B0_original", source_hash,
        wrong_parameter=family == "parameter", fault=family if family != "parameter" else None)
    bundle = load_trace(b0["run_dir"])
    diagnosis = classify_bundle(bundle)
    result = {"family": family, "case_id": "MATCHED-" + family.upper(), "protocol_version": VERSION,
        "expected": FAMILIES[family], "backend": "real_gimp", "b0": b0,
        "diagnosis": diagnosis.model_dump(), "formal_eligible": initial_failure_observed(family, b0),
        "status": "OBSERVED_FAILURE"}
    if not result["formal_eligible"]:
        result["status"] = "BLOCKED_BEFORE_DECLARED_FAILURE"
        dump(root / "case_result.json", result)
        return result
    b1 = execute_condition(task, root / "B1_naive_retry", source_hash, wrong_parameter=family == "parameter")
    b1_contract = contract().model_copy(update={"required_checks": ["process", "artifact", "task", "source_unchanged"]})
    b1["verification"] = RecoveryVerifier().verify(
        session=RecoverySession(run_id=Path(b0["run_dir"]).name, task_id=task["task_id"], trigger_reason="naive_retry"),
        attempt=b1["measurements"], contract=b1_contract).model_dump()
    result["b1"] = b1
    action = ({"type": "export_file", "path": "output.png", "format": "png"} if family == "fileio"
              else {"type": "resize_image", "width": 512, "height": 512})
    b2_record = {}
    def executor(selected):
        condition = execute_condition(task, root / "B2_diagagent", source_hash, repair_action=selected)
        b2_record.update(condition)
        return condition["measurements"]
    started = time.monotonic()
    healing = HealingLoop(policy=ReplayPolicy()).run(
        run_context={"run_id": Path(b0["run_dir"]).name, "task_id": task["task_id"], "human_approved": False,
                     "current_state": {"repair_action": action, "reversible": True, "low_risk": True}},
        diagnosis=diagnosis, evidence_graph=build_evidence_graph(bundle, diagnosis), task_spec=task,
        executor=executor, run_dir=b0["run_dir"], max_recovery_attempts=1)
    result["b2"] = {**b2_record, "healing": healing.model_dump(), "recovery_time_seconds": time.monotonic() - started}
    result["status"] = "COMPLETED"
    dump(root / "case_result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(FAMILIES), action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if len(set(args.case)) != len(args.case):
        parser.error("Duplicate cases are not permitted")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = load_task(TASK)
    registration = {"protocol_version": VERSION, "registered_at": datetime.now(timezone.utc).isoformat(),
        "cases": args.case, "faults": {k: FAMILIES[k] for k in args.case},
        "task": str(TASK.relative_to(ROOT)), "task_sha256": sha(TASK),
        "input_sha256": sha(task["initial_state"]["input_file"]),
        "source_sha256": GIMPEnvironment.source_fingerprint(), "config": DesktopConfig().model_dump(),
        "max_recovery_attempts": 1, "restart_mode": "fresh_task_replay", "automatic_retry": False,
        "diagnosis": "classify_bundle; unmodified confidence; no model",
        "repair_provider": "declared oracle task scaffold, guarded concrete action",
        "side_effect_scope": "source hash, output handle, size, full-resize RGB MAE <= 8; not general semantic safety",
        "ground_truth": "declared injection and observed effect; family attribution remains rule-based",
        "comparison": "B0 common original; B1/B2 each one fresh replay; transient faults clear equally",
        "environment_failures": "retain every registered case; report admission coverage; never cherry-pick"}
    dump(output / "protocol.json", registration)
    results = []
    for family in args.case:
        print(f"START {family}", flush=True)
        try:
            result = one_case(family, output, task, registration)
        except Exception as error:
            result = {"family": family, "status": "PROTOCOL_ERROR", "formal_eligible": False,
                      "error": f"{type(error).__name__}: {error}"}
            dump(output / family / "protocol_error.json", result)
        results.append(result)
        print(json.dumps({"family": family, "status": result["status"], "admitted": result["formal_eligible"],
              "b2": result.get("b2", {}).get("healing", {}).get("outcome")}), flush=True)
    unchanged = GIMPEnvironment.source_fingerprint() == registration["source_sha256"] and sha(TASK) == registration["task_sha256"]
    dump(output / "manifest.json", {"protocol_version": VERSION, "protocol_sha256": sha(output / "protocol.json"),
        "source_unchanged_during_run": unchanged, "cases": results})
    return results


if __name__ == "__main__":
    main()
