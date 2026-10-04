import json

from diagagent.metrics.recovery import compute_recovery_metrics, write_recovery_report


def test_recovery_metrics_keep_real_gimp_denominator_separate(tmp_path):
    records = [
        {"case_id": "synthetic", "backend": "mock", "repairability": "REPAIRABLE",
         "recovery_success": True, "predicted_recovered": True, "failure_type": "Parameter Error",
         "first_failure_step": 2, "diagnosis": {"type": "Parameter Error", "step": 2}},
        {"case_id": "real", "backend": "real_gimp", "repairability": "REPAIRABLE",
         "recovery_success": False, "predicted_recovered": True, "failure_type": "Parameter Error",
         "first_failure_step": 2, "diagnosis": {"type": "Tool Selection Error", "step": 3}},
    ]
    metrics = compute_recovery_metrics(records)
    assert metrics["recovery_success_rate"] == 0.5
    assert metrics["recovery_precision"] == 0.5
    assert metrics["real_gimp_denominators"] == {"repairable": 1, "successful": 0}
    assert metrics["localization_accuracy"] == 0.5


def test_recovery_report_writes_json_csv_and_markdown(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("cases:\n  - case_id: c1\n    backend: mock\n    repairability: REPAIRABLE\n    recovery_success: true\n", encoding="utf-8")
    paths = write_recovery_report(manifest, tmp_path / "out")
    assert all(path.exists() for path in paths.values())
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["successful_recoveries"] == 1
    assert "Recovery Metrics" in paths["report"].read_text(encoding="utf-8")
