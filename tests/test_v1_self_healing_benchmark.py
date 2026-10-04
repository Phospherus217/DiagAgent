from diagagent.experiments.self_healing_benchmark import evaluate_self_healing_cases


def test_self_healing_benchmark_keeps_three_policies_distinct():
    result = evaluate_self_healing_cases([{
        "case_id": "SH-001", "backend": "synthetic", "failure_type": "Parameter Error",
        "B0_no_repair": {"recovery_success": False},
        "B1_naive_retry": {"recovery_success": True, "predicted_recovered": True},
        "B2_diagagent_healing": {"repairability": "REPAIRABLE", "recovery_success": True,
                                  "predicted_recovered": True},
    }])
    assert result["baselines"] == ["B0_no_repair", "B1_naive_retry", "B2_diagagent_healing"]
    assert result["results"]["B2_diagagent_healing"]["metrics"]["successful_recoveries"] == 1
