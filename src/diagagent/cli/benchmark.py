"""Implementation of 'diagagent benchmark' command."""

from pathlib import Path
from typing import List
import yaml
from diagagent.tasks import load_task

from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment


def run_cli_benchmark(
    tasks_dir: str = "benchmark/tasks",
    backend: str = "mock",
    output_dir: str = "runs/benchmark",
) -> None:
    """Runs all benchmark tasks in tasks_dir and prints aggregate metrics."""
    p = Path(tasks_dir).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Benchmark tasks directory not found: {p}")

    task_files = sorted(list(p.glob("*.yaml")))
    if not task_files:
        print(f"No task specification YAML files found in: {p}")
        return

    print("=" * 80)
    print(f"DiagAgent Benchmark Suite — Running {len(task_files)} Professional GUI Tasks")
    print(f"Backend: {backend} | Output Directory: {output_dir}")
    print("=" * 80)

    results = []

    for tf in task_files:
        task_spec = load_task(tf)

        task_id = task_spec.get("task_id", tf.stem)
        capability = task_spec.get("capability", "General")
        instruction = task_spec.get("instruction", "")

        env = GIMPEnvironment(backend=backend, run_root=output_dir)
        agent = RuleAgent()
        runtime = AgentRuntime(env=env)

        try:
            report = runtime.run_task(task_spec=task_spec, agent=agent)
            results.append({
                "task_id": task_id,
                "capability": capability,
                "success": report.success,
                "status": report.task_status,
                "steps": report.total_steps,
                "first_failure": report.first_failure_step if not report.success else "-",
                "error_type": report.failure_type if not report.success else "-",
            })
        finally:
            env.close()

    # Print summary table
    print("\nBenchmark Execution Results:")
    print("-" * 80)
    header = f"{'Task ID':<22} | {'Capability':<22} | {'Status':<6} | {'Steps':<5} | {'First Failure'}"
    print(header)
    print("-" * 80)
    passed_count = 0
    for r in results:
        status_str = r["status"]
        if r["success"]:
            passed_count += 1
        fail_str = f"Step {r['first_failure']} ({r['error_type']})" if r["first_failure"] != "-" else "-"
        print(f"{r['task_id']:<22} | {r['capability']:<22} | {status_str:<6} | {r['steps']:<5} | {fail_str}")
    print("-" * 80)
    pass_rate = (passed_count / len(results)) * 100.0 if results else 0.0
    failed = sum(r["status"] == "FAIL" for r in results)
    unknown = sum(r["status"] == "UNKNOWN" for r in results)
    print(f"Total: {len(results)} | Passed: {passed_count} | Failed: {failed} | Unknown: {unknown} | Success Rate: {pass_rate:.1f}%")
    print("=" * 80)
