import json

import pytest

from diagagent.diagnosis.evidence_formatter import format_evidence
from diagagent.diagnosis.llm_reasoner import LLMReasoner
from diagagent.diagnosis.trace_loader import TraceBundle
from diagagent.evaluation.benchmark import evaluate_cases
from diagagent.experiments.real_repair import run_real_repair_manifest, validate_case_manifest
from diagagent.models.base import ModelResponse
from diagagent.schemas.diagnosis import DiagnosisResult


def make_bundle(tmp_path, failed=True):
    (tmp_path / "trace.jsonl").write_text(json.dumps({
        "step": 2, "action": {"type": "resize_image", "width": 128},
        "step_eval": {"error_type": "Parameter Error"} if failed else {"subgoal_completed": True},
    }) + "\n", encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(json.dumps({"event": "run_finished", "success": not failed}) + "\n", encoding="utf-8")
    (tmp_path / "metadata.json").write_text(json.dumps({"task_id": "resize"}), encoding="utf-8")
    (tmp_path / "evaluation.json").write_text(json.dumps({"artifact": {"status": "FAIL" if failed else "PASS", "artifact_pass": not failed}}), encoding="utf-8")
    from diagagent.diagnosis.trace_loader import load_trace
    return load_trace(tmp_path)


def test_evidence_formatter_excludes_task_gold_and_raw_model_payload(tmp_path):
    bundle = make_bundle(tmp_path)
    evidence = format_evidence(bundle)
    assert "task_spec" not in evidence
    assert "raw" not in json.dumps(evidence)
    assert evidence["steps"][0]["step"] == 2


def test_llm_reasoner_cannot_override_rule_failure_or_step(tmp_path):
    class Model:
        def query(self, messages):
            return ModelResponse(text=json.dumps({
                "failure_type": "Artifact Error", "first_failure_step": 99,
                "confidence": 0.99, "explanation": "Evidence explanation", "repair_suggestion": "Fix it",
            }))
    bundle = make_bundle(tmp_path)
    rule = DiagnosisResult(run_id=bundle.run_id, failure=True, type="Parameter Error",
                           failure_type="Parameter Error", step=2, first_failure_step=2,
                           confidence="high", confidence_score=0.92,
                           explanation="Rule evidence")
    result = LLMReasoner(Model()).reason(bundle, rule)
    assert result.evidence_bound and result.final_failure_type == "Parameter Error"
    assert result.final_first_failure_step == 2
    assert result.llm_available is True


def test_benchmark_compares_three_baselines(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    bundle = make_bundle(case_dir)
    result = evaluate_cases([{"case_id": "case1", "run_dir": "case", "failure_type": "Parameter Error", "first_failure_step": 2}], tmp_path)
    assert set(result["metrics"]) == {"outcome_only", "trace_only", "diagagent"}
    assert result["metrics"]["diagagent"]["accuracy"] == 1.0
    assert result["metrics"]["diagagent"]["macro_f1"] == 1.0


def test_real_repair_manifest_is_five_cases_and_dry_run_is_explicit(tmp_path):
    cases = [{"case_id": f"case{i}", "task_file": "task.yaml", "failure_type": "Parameter Error", "first_failure_step": 2,
              "automatic_retry": False} for i in range(5)]
    assert len(validate_case_manifest(cases)) == 5
    manifest = tmp_path / "cases.yaml"
    import yaml
    manifest.write_text(yaml.safe_dump({"cases": cases}), encoding="utf-8")
    output = run_real_repair_manifest(manifest, tmp_path / "runs")
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["execute"] is False and summary["real_gimp_evidence"] is False
    assert all(item["status"] == "PLANNED" for item in summary["cases"])


def test_real_manifest_rejects_implicit_retry():
    cases = [{"case_id": f"case{i}", "task_file": "task.yaml", "failure_type": "Parameter Error", "first_failure_step": 2,
              "automatic_retry": True} for i in range(5)]
    with pytest.raises(ValueError, match="automatic retry"):
        validate_case_manifest(cases)
