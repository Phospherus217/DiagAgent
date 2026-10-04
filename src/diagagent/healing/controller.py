"""High-level v1.0 healing controller.

The controller deliberately exposes a dry-run-only filesystem path.  A real
executor must be supplied by a caller through the lower-level runtime APIs;
this module never invents GUI actions or silently retries a failed run.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import load_trace
from diagagent.healing.loop import HealingLoop
from diagagent.schemas.recovery import HealingRunResult


class HealingController:
    """Compose trace loading, diagnosis, evidence graph and bounded policy."""

    def __init__(self, *, loop: Optional[HealingLoop] = None):
        self.loop = loop or HealingLoop()

    def dry_run(self, run_dir: Union[str, Path], *, task_spec: Optional[Dict[str, Any]] = None) -> HealingRunResult:
        root = Path(run_dir).resolve()
        bundle = load_trace(root)
        diagnosis = classify_bundle(bundle)
        graph = build_evidence_graph(bundle, diagnosis)
        context = {"run_id": bundle.run_id, "task_id": bundle.task_id or bundle.task,
                   "current_state": {"backend": bundle.metadata.get("backend", "unknown")}}
        return self.loop.dry_run(run_context=context, diagnosis=diagnosis,
                                 evidence_graph=graph, task_spec=task_spec or {}, run_dir=root)


def run_healing_dry_run(run_dir: Union[str, Path], **kwargs: Any) -> HealingRunResult:
    return HealingController().dry_run(run_dir, **kwargs)


__all__ = ["HealingController", "run_healing_dry_run"]
