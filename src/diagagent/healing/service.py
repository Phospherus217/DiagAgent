"""Filesystem entry points for v1.0 healing dry-runs."""

import json
from pathlib import Path
from typing import Union

import yaml

from diagagent.diagnosis.classifier import classify_bundle
from diagagent.diagnosis.graph import build_evidence_graph
from diagagent.diagnosis.trace_loader import load_trace
from diagagent.healing.loop import HealingLoop
from diagagent.healing.controller import HealingController


def heal_run_dry_run(run_dir: Union[str, Path]):
    root = Path(run_dir).resolve()
    task_path = root / "task_spec.yaml"
    task_spec = yaml.safe_load(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
    # Keep the established service entry point while routing through the
    # controller so the complete v1.0 pipeline is covered by one API.
    return HealingController().dry_run(root, task_spec=task_spec or {})


__all__ = ["heal_run_dry_run"]
