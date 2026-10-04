"""DualEvaluator coordinating process step evaluation and artifact evaluation."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from diagagent.evaluator.artifact_evaluator import ArtifactEvaluator
from diagagent.evaluator.models import ArtifactEvaluation, StepEvaluation
from diagagent.evaluator.step_evaluator import StepEvaluator
from diagagent.observation.models import Observation


class DualEvaluator:
    """Combines fine-grained step evaluation with final artifact verification."""

    def __init__(self, task_spec: Dict[str, Any]):
        self.task_spec = task_spec
        self.step_evaluator = StepEvaluator(task_spec)
        self.artifact_evaluator = ArtifactEvaluator(task_spec)

    def evaluate_step(
        self,
        step_index: int,
        action: Any,
        obs_before: Optional[Observation] = None,
        obs_after: Optional[Observation] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> StepEvaluation:
        """Evaluates an individual step in the trajectory."""
        return self.step_evaluator.evaluate_step(
            step_index=step_index,
            action=action,
            obs_before=obs_before,
            obs_after=obs_after,
            execution_result=execution_result,
        )

    def evaluate_artifact(self, artifact_path: Union[str, Path]) -> ArtifactEvaluation:
        """Evaluates the final artifact on disk."""
        return self.artifact_evaluator.evaluate(artifact_path)
