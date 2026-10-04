"""Legacy tasks execute; unsupported text semantics remain UNKNOWN."""

from pathlib import Path
import yaml

from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment


def test_all_six_benchmark_tasks(tmp_path):
    tasks_dir = Path(__file__).parent.parent / "benchmark" / "tasks"
    task_files = sorted(list(tasks_dir.glob("*.yaml")))
    assert len(task_files) == 6, f"Expected 6 benchmark tasks, found {len(task_files)}"

    for tf in task_files:
        with open(tf, "r", encoding="utf-8") as f:
            task_spec = yaml.safe_load(f)

        # Make input file point to real asset
        asset_file = Path(__file__).parent.parent / task_spec["initial_state"]["input_file"]
        task_spec["initial_state"]["input_file"] = str(asset_file)

        env = GIMPEnvironment(backend="mock", run_root=tmp_path / "runs")
        agent = RuleAgent()
        runtime = AgentRuntime(env=env)

        try:
            report = runtime.run_task(task_spec=task_spec, agent=agent)
            if tf.stem in {"add_text", "crop_text", "text_export"}:
                assert report.task_status == "UNKNOWN" and report.artifact_status == "UNKNOWN"
                assert not report.success
            else:
                assert report.success, report.model_dump()
        finally:
            env.close()
