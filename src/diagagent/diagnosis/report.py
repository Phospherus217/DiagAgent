"""Diagnostic report model and multi-target formatters."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from diagagent.diagnosis.taxonomy import FailureCategory, get_responsibility


def write_diagnostic_report(run_dir, output_dir=None):
    """Create an immutable report directory, never replacing historical results."""
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    from uuid import uuid4
    from diagagent.diagnosis.trace_loader import load_trace
    from diagagent.diagnosis.classifier import classify_bundle
    from diagagent.diagnosis.first_failure import detect_first_failure
    from diagagent.diagnosis.graph import build_evidence_graph
    from diagagent.trace.redaction import Redactor

    root = Path(run_dir).resolve()
    bundle = load_trace(root)
    result = classify_bundle(bundle)
    target = Path(output_dir).resolve() if output_dir else root / "diagnoses" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid4().hex[:8])
    target.mkdir(parents=True, exist_ok=False)
    payload = Redactor().clean(result.model_dump())
    graph = build_evidence_graph(bundle, result)
    for name, value in (("diagnosis.json", payload), ("first_failure.json", detect_first_failure(bundle)),
                        ("evidence_graph.json", graph), ("sources.json", bundle.source_hashes)):
        with (target / name).open("x", encoding="utf-8") as stream:
            json.dump(Redactor().clean(value), stream, ensure_ascii=False, indent=2)
    lines = ["# Diagnostic Loop Report", "", f"Run: {bundle.run_id}", "",
             f"Status: {result.diagnosis_status}", "",
             f"First Failure: {result.first_failure_step if result.first_failure_step is not None else 'Not localized to an action'}",
             f"Failure: {result.failure_type or 'Not established'}",
             f"Responsibility: {result.layer or 'Not established'}", "",
             "Earliest observed failure; causal irrecoverability is not established.", "", "## Evidence", ""]
    for item in payload["evidence"]:
        lines.extend([f"Source: {item['source']}:{item.get('line') or ''}", "",
                      "```json", json.dumps(item["content"], ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["## Suggested Repair", "", result.suggested_repair or "Inspect the evidence and provide human feedback.", ""])
    if bundle.warnings:
        lines.extend(["## Evidence limitations", "", *bundle.warnings])
    (target / "diagnosis.md").write_text("\n".join(lines), encoding="utf-8")
    return target


class StepExecutionSummary(BaseModel):
    step: int
    name: str
    status: str  # "PASS" or "FAIL"
    error_type: Optional[str] = None
    note: Optional[str] = None


class DiagnosticReport(BaseModel):
    """Auditable diagnostic report produced after evaluating task execution."""
    model_config = ConfigDict(extra="allow")

    task_id: str
    task_instruction: str = ""
    success: bool = False
    task_status: str = "UNKNOWN"
    process_status: str = "UNKNOWN"
    artifact_status: str = "UNKNOWN"
    has_observed_failure: bool = False
    attribution_status: str = "unknown"
    total_steps: int = 0
    first_failure_step: Optional[int] = None
    first_failure_subgoal: Optional[str] = None
    failure_type: Optional[str] = None
    failure_layer: Optional[str] = None
    responsibility: Optional[FailureCategory] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    recommendation: Optional[str] = None
    step_summaries: List[StepExecutionSummary] = Field(default_factory=list)
    diagnosis_version: Optional[str] = None
    failure: Optional[bool] = None
    step: Optional[int] = None
    type: Optional[str] = None
    layer: Optional[str] = None
    confidence: str = "unknown"
    v02_attribution: Dict[str, Any] = Field(default_factory=dict)
    diagnosis_status: str = "INSUFFICIENT_EVIDENCE"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def format_cli(self) -> str:
        """Formats report according to Section 7 Demo Goal specification."""
        lines = []
        task_label = self.task_instruction or self.task_id
        lines.append(f"Task: {task_label}")
        lines.append("")
        lines.append("Execution:")
        for s in self.step_summaries:
            status_text = s.status.upper()
            lines.append(f"  {s.step} {s.name} {status_text}")

        lines.append("")
        lines.append("Diagnosis:")
        lines.append(f"  Task / Process / Artifact: {self.task_status} / {self.process_status} / {self.artifact_status}")
        if self.success:
            lines.append("  Status: Required task checks passed.")
            lines.append("  Result: PASS")
        if not self.success or self.has_observed_failure:
            location = f"Step {self.first_failure_step}" if self.first_failure_step is not None else "Not localized to an action"
            lines.append(f"  First Failure: {location}")
            lines.append(f"  Type: {self.failure_type or 'Not established'}")
            if self.responsibility:
                lines.append(f"  Responsibility: {self.responsibility.value}")

            evidence_items = []
            for k, v in self.evidence.items():
                evidence_items.append(f"{k}={v}")
            evidence_str = ", ".join(evidence_items) if evidence_items else "No explicit parameter mismatch"
            lines.append(f"  Evidence: {evidence_str}")

            if self.recommendation:
                lines.append(f"  Recommendation: {self.recommendation}")

        if self.diagnosis_version == "0.2":
            lines.append(f"  v0.2 observed failure: {self.type or self.diagnosis_status}; step={self.step}; responsibility={self.layer}; confidence={self.confidence}")
        return "\n".join(lines)
