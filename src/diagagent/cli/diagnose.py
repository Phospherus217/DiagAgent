"""Read the original report or write a new, bounded evidence recomputation."""
from datetime import datetime, timezone
import json
from pathlib import Path
import yaml
from diagagent.diagnosis.engine import DiagnosisEngine
from diagagent.diagnosis.report import DiagnosticReport
from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.evaluator.step_evaluator import StepEvaluator
from diagagent.observation.models import Observation
from diagagent.trace.paths import evidence_path
from diagagent.tasks import output_name


def recompute_run(run_dir):
    root = Path(run_dir).resolve()
    warnings, steps, evaluations = [], [], []
    task = {"task_id": root.name}
    task_available = False
    try:
        loaded = yaml.safe_load((root / "task_spec.yaml").read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("success_criteria"), dict):
            raise ValueError("Missing task evaluation contract")
        task, task_available = loaded, True
    except (OSError, ValueError, yaml.YAMLError) as error:
        warnings.append("Task unavailable: " + type(error).__name__)
    try:
        for index, line in enumerate((root / "trace.jsonl").read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("step"), int):
                    raise ValueError("Invalid semantic record")
                steps.append(row)
            except ValueError:
                warnings.append(f"Corrupt/incomplete trace line {index}")
    except OSError:
        warnings.append("Trace unavailable")
    if not steps:
        warnings.append("No process evidence")
    evaluator = StepEvaluator(task)
    legacy = False
    for row in steps:
        if row.get("schema_version") != "2.0":
            legacy = True
            if row.get("step_eval"):
                evaluations.append(dict(row["step_eval"], evidence_scope="legacy_stored_check"))
            continue
        after = row.get("obs_after")
        if after is None or not evidence_path(root, after.get("screenshot_path")):
            warnings.append(f"Screenshot unavailable at step {row.get('step')}")
        if row.get("phase") == "failure":
            evaluations.append(row.get("step_eval") or {"step": row["step"], "status": "UNKNOWN"})
        elif row.get("phase") != "final":
            action = row.get("action") or ({"type": "open_image"} if row.get("phase") == "reset" else None)
            if action:
                try:
                    evaluations.append(evaluator.evaluate_step(row["step"], action,
                        Observation(**row["obs_before"]) if row.get("obs_before") else None,
                        Observation(**after) if after else None, row.get("execution_result")).model_dump())
                except (ValueError, TypeError, KeyError, AttributeError):
                    warnings.append(f"Unable to evaluate step {row['step']}")
    name = output_name((task.get("initial_state") or {}).get("output_file", "output.png"))
    artifact = evidence_path(root, "artifacts/" + name)
    if not task_available:
        artifact_result = {"status": "UNKNOWN", "artifact_pass": False,
                           "error_type": "Evaluator Error", "metrics": {"warnings": warnings}}
    else:
        artifact_result = ArtifactEvaluator(task).evaluate(artifact or root / "artifacts" / "__missing__").model_dump()
    report = DiagnosisEngine.diagnose(task, evaluations, artifact_result)
    if warnings and report.process_status == "PASS":
        report.process_status = "UNKNOWN"
    if legacy:
        warnings.append("Legacy process checks retained, not upgraded to v2 effect evidence")
    try:
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
        if result.get("termination_reason") not in {"agent_stop", "completed"}:
            warnings.append("Run did not record a normal agent stop")
    except (OSError, ValueError):
        warnings.append("Incomplete run: result.json unavailable")
    if warnings and report.task_status == "PASS":
        report.task_status, report.success = "UNKNOWN", False
    from diagagent.diagnosis.classifier import classify
    from diagagent.diagnosis.evidence import load_evidence
    _, events, _, event_warnings = load_evidence(root)
    enriched = {**report.model_dump(), **classify(steps, events, report.model_dump())}
    return dict(evaluator_version="v2-evidence", created_at=datetime.now(timezone.utc).isoformat(),
                warnings=warnings + event_warnings, diagnosis=enriched, artifact=artifact_result, steps=evaluations)


def diagnose_cli_run(run_path, recompute=False):
    root = Path(run_path).resolve()
    if not recompute and (root / "diagnosis.json").exists():
        stored = json.loads((root / "diagnosis.json").read_text(encoding="utf-8"))
        if "task_status" not in stored:
            print("Legacy stored diagnosis: original success is not a v2 evidence certification.")
            print(f"Original recorded success: {stored.get('success', 'unavailable')}")
        report = DiagnosticReport(**stored)
        print(report.format_cli())
        return root / "diagnosis.json"
    payload = recompute_run(root)
    folder = root / "diagnoses"
    folder.mkdir(exist_ok=True)
    target = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + ".json")
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
    print(DiagnosticReport(**payload["diagnosis"]).format_cli())
    print(f"Versioned diagnosis: {target}")
    return target
