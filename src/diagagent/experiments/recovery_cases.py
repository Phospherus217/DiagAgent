"""Offline adapters for the three existing registered recovery families.

All actions run through AgentRuntime and the existing mock backend. This is
fixture validation, never real-GIMP acceptance or new experimental evidence.
"""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from diagagent.agent.runtime import AgentRuntime
from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import load_trace
from diagagent.environment.errors import DesktopError
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.experiments.portfolio_protocol import (
    ROOT, ReplayPolicy, SequenceAgent, contract, dump, inspect_run, read, sha,
)
from diagagent.healing.loop import HealingLoop
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.recovery import RecoverySession
from diagagent.tasks import load_task

MODES = ("no_recovery", "direct_retry", "constrained_recovery")


def registered_cases():
    return read(ROOT / "benchmark/recovery_cases.json")["cases"]


def measure_run(run_dir, task, source_hash, repair_action=None):
    """Re-read the trace and actual PNG; never use an agent success claim."""
    root = Path(run_dir)
    measured = inspect_run(root, task, source_hash, repair_action)
    artifact = ArtifactEvaluator(task).evaluate(root / "artifacts/output.png")
    measured["artifact_recovered"] = artifact.status == "PASS"
    measured["task_recovered"] = measured["process_recovered"] and measured["artifact_recovered"]
    # Mock execution records semantic export handles instead of OS key presses.
    bundle = load_trace(root)
    exports = [row["action"] for row in bundle.steps
               if (row.get("action") or {}).get("type") == "export_file"]
    measured["side_effect_checks"]["output_path_escape"] = bool(exports) and all(
        action.get("path") == task["initial_state"]["output_file"] for action in exports)
    measured["artifact_measurement"] = artifact.model_dump()
    measured["evidence_root"] = str(root)
    return measured


def execute_fixture(task, output, source_hash, *, family=None, wrong_parameter=False, repair_action=None):
    env = GIMPEnvironment(backend="mock", run_root=output)
    if family in {"dialog", "fileio"}:
        def fail(*args, **kwargs):
            kind = "Dialog Operation Error" if family == "dialog" else "File I/O Error"
            raise DesktopError("Deterministic fixture boundary fault", kind)
        if family == "dialog":
            env.backend.resize_image = fail
        else:
            env.backend.export_file = fail
    AgentRuntime(env, automatic_recovery=False).run_task(
        task, SequenceAgent(wrong_parameter=wrong_parameter, repair_action=repair_action))
    root = env.trace_recorder.run_dir
    metadata = read(root / "metadata.json")
    env.trace_recorder.save_json("metadata.json", {**metadata, "evidence_kind": "deterministic_fixture",
                                                  "real_gui_acceptance": False})
    measured = measure_run(root, task, source_hash, repair_action)
    dump(root / "fixture_measurements.json", measured)
    return root, measured


def run_case(case_id, mode, output):
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")
    entries = {entry["case_id"]: entry for entry in registered_cases()}
    if case_id not in entries:
        raise ValueError(f"Unknown registered case: {case_id}")
    entry = entries[case_id]
    family = entry["family"]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = load_task(ROOT / "benchmark/tasks/resize_image.yaml")
    source_hash = sha(task["initial_state"]["input_file"])
    original, original_measurement = execute_fixture(
        task, output / "original", source_hash, family=family, wrong_parameter=family == "parameter")
    bundle = load_trace(original)
    diagnosis = classify_bundle(bundle)
    graph = build_evidence_graph(bundle, diagnosis)
    dump(original / "fixture_diagnosis.json", diagnosis.model_dump())
    dump(original / "fixture_evidence_graph.json", graph)
    result = {"case_id": case_id, "mode": mode, "evidence_kind": "deterministic_fixture",
              "real_gui_acceptance": False, "task_id": task["task_id"],
              "original_run": str(original.relative_to(output)), "original_failure": diagnosis.failure_type,
              "first_failure_step": diagnosis.first_failure_step, "diagnosis": diagnosis.model_dump(),
              "attempts": 0, "max_attempts": 1, "guard_decisions": [],
              "process_pass": original_measurement["process_recovered"],
              "artifact_pass": original_measurement["artifact_recovered"],
              "recovered": False, "final_status": "NO_RECOVERY"}
    if mode == "direct_retry":
        replay, measured = execute_fixture(task, output / "retry", source_hash, wrong_parameter=family == "parameter")
        verification = RecoveryVerifier().verify(
            session=RecoverySession(run_id=bundle.run_id, task_id=task["task_id"], trigger_reason="direct_retry"),
            attempt=measured, contract=contract())
        result.update(attempts=1, recovery_run=str(replay.relative_to(output)),
                      process_pass=verification.process_recovered, artifact_pass=verification.artifact_recovered,
                      recovered=verification.recovered, verification=verification.model_dump(),
                      final_status="RECOVERED" if verification.recovered else "NOT_RECOVERED")
    elif mode == "constrained_recovery":
        action = ({"type": "export_file", "path": "output.png", "format": "png"} if family == "fileio"
                  else {"type": "resize_image", "width": 512, "height": 512})
        def executor(selected):
            replay, measured = execute_fixture(task, output / "repair", source_hash, repair_action=selected)
            result["recovery_run"] = str(replay.relative_to(output))
            return measured
        healing = HealingLoop(policy=ReplayPolicy()).run(
            run_context={"run_id": bundle.run_id, "task_id": task["task_id"],
                         "current_state": {"repair_action": action, "reversible": True, "low_risk": True}},
            diagnosis=diagnosis, evidence_graph=graph, task_spec=task, executor=executor, run_dir=original)
        session = read(Path(healing.session_path) / "session.json")
        verification = session.get("verification_result") or {}
        result.update(attempts=session["attempt_count"],
                      guard_decisions=[session["guard_decision"]] if session.get("guard_decision") else [],
                      process_pass=verification.get("process_recovered", False),
                      artifact_pass=verification.get("artifact_recovered", False),
                      recovered=healing.recovery_success is True, final_status=healing.outcome,
                      recovery_session=str(Path(healing.session_path).relative_to(output)),
                      verification=verification)
    dump(output / "case_result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=[row["case_id"] for row in registered_cases()])
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--output", type=Path, help="New directory; existing outputs are never overwritten.")
    args = parser.parse_args(argv)
    output = args.output or Path("runs/recovery_cases") / f"{args.case}_{args.mode}_{uuid4().hex[:8]}"
    result = run_case(args.case, args.mode, output)
    print(json.dumps({"result": str(output / "case_result.json"), **{
        key: result[key] for key in ("case_id", "mode", "original_failure", "attempts", "guard_decisions",
                                    "process_pass", "artifact_pass", "recovered", "final_status")}}, indent=2))
    return result


if __name__ == "__main__":
    main()
