"""Injected real-GIMP recovery executor adapter.

The adapter owns no lowering rules.  ``GIMPEnvironment.execute`` remains the
single path that performs semantic lowering, structural lowering and GUI
dispatch.  This class only translates its persisted result into the
independent verification fields expected by ``HealingLoop``.
"""

from __future__ import annotations

from typing import Any, Dict

from diagagent.actions.parser import ActionParser


class RealGIMPRecoveryExecutor:
    def __init__(self, env: Any):
        if getattr(env, "backend_name", None) != "real_gimp":
            raise ValueError("RealGIMPRecoveryExecutor requires backend=real_gimp")
        self.env = env

    def execute(self, action: Dict[str, Any], *, finalize: bool = True) -> Dict[str, Any]:
        parsed = ActionParser.parse(action)
        _, _, _, info = self.env.execute(parsed)
        execution = (info or {}).get("execution_result") or {}
        effect = execution.get("effect_evidence") or {}
        final = self.env.evaluate_final() if finalize else {}
        artifact = final.get("artifact_evaluation") or {}
        summary = final.get("summary") or {}
        trace_root = getattr(getattr(self.env, "trace_recorder", None), "run_dir", None)
        refs = ["trace.jsonl", "events.jsonl", "screenshots/"]
        if trace_root is not None:
            refs = [str(trace_root / item) for item in refs]
        # The executor result is deliberately not treated as task recovery:
        # process and artifact/task checks remain separate signals.
        process = bool(execution.get("success")) and effect.get("status") == "PASS"
        return {
            "success": execution.get("success"),
            "process_recovered": process,
            "artifact_recovered": artifact.get("artifact_pass") is True,
            "task_recovered": summary.get("success") is True,
            "executor_success": execution.get("success"),
            "effect_evidence": effect,
            "evidence_refs": refs,
            "artifact_evaluation": artifact,
            "task_summary": summary,
        }


__all__ = ["RealGIMPRecoveryExecutor"]
