"""Implementation of 'diagagent run' command."""

import math
import os
from pathlib import Path
from typing import Optional
import yaml
from diagagent.config import DesktopConfig
from diagagent.tasks import load_task

from diagagent.agent.mock_agent import MockFaultAgent
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.environment.gimp_env import GIMPEnvironment


def run_cli_task(
    task_file: str,
    agent_type: str = "rule",
    fault_type: str = "parameter",
    fault_step: int = 3,
    backend: str = "mock",
    output_dir: str = "runs",
    config_file: Optional[str] = None,
    model_type: Optional[str] = None,
    automatic_recovery: bool = True,
):
    """Executes a benchmark task and prints the diagnostic execution report."""
    task_path = Path(task_file).resolve()
    if not task_path.exists():
        raise FileNotFoundError(f"Task specification file not found: {task_path}")

    task_spec = load_task(task_path)

    model = None
    agent = None
    agent_type_clean = agent_type.lower().strip()
    if model_type or agent_type_clean in {"model", "vlm"}:
        m_type = (model_type or "mock").lower().strip()
        if m_type in {"openai", "openai_compat", "vlm"}:
            from diagagent.models.openai_compatible import OpenAICompatibleModel
            model_config = {
                "api_key": os.getenv("DIAGAGENT_API_KEY"),
                "base_url": os.getenv("DIAGAGENT_BASE_URL"),
                "model_name": os.getenv("DIAGAGENT_MODEL"),
            }
            for field, variable in (
                ("api_key", "DIAGAGENT_API_KEY"),
                ("base_url", "DIAGAGENT_BASE_URL"),
                ("model_name", "DIAGAGENT_MODEL"),
            ):
                if not model_config[field] or not model_config[field].strip():
                    raise ValueError(f"{variable} must be set and non-empty for model execution")
            timeout_error = "DIAGAGENT_MODEL_TIMEOUT must be a finite positive number of seconds"
            try:
                timeout = float(os.getenv("DIAGAGENT_MODEL_TIMEOUT", "30"))
            except ValueError:
                raise ValueError(timeout_error) from None
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError(timeout_error)
            model = OpenAICompatibleModel(**model_config, timeout=timeout)
        else:
            from diagagent.models.mock import MockModel
            model = MockModel()
    elif agent_type_clean in {"mock_fault", "fault"}:
        agent = MockFaultAgent(fault_type=fault_type, fault_step=fault_step)
    elif agent_type_clean == "rule":
        agent = RuleAgent()
    else:
        raise ValueError(f"Unsupported agent: {agent_type}; supports rule, mock_fault, and model")

    # Initialize environment & runtime
    env = GIMPEnvironment(backend=backend, run_root=output_dir, config=DesktopConfig.load(config_file))
    runtime = AgentRuntime(env=env, model=model, automatic_recovery=automatic_recovery)

    try:
        report = runtime.run_task(task_spec=task_spec, agent=agent, model=model)
        # Print formatted report
        print(report.format_cli())
        print(f"Run directory: {env.trace_recorder.run_dir}")
        return report
    finally:
        env.close()
