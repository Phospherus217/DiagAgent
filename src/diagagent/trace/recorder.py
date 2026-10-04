"""Trace recorder managing disk persistence for execution logs, screenshots, and artifacts."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from diagagent.trace.models import RunSummary, TraceStepRecord
from diagagent.trace.redaction import Redactor


class TraceRecorder:
    """Records full agent trajectories and manages run directory artifacts."""

    def __init__(self, run_dir: Union[str, Path], task_id: str, run_id: Optional[str] = None):
        self.task_id = task_id
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.run_id = run_id or f"{task_id}_{timestamp_str}"
        self.run_dir = Path(run_dir) / self.run_id
        self.screenshots_dir = self.run_dir / "screenshots"
        self.artifacts_dir = self.run_dir / "artifacts"
        self.trace_file = self.run_dir / "trace.jsonl"
        self.result_file = self.run_dir / "result.json"
        self.diagnosis_file = self.run_dir / "diagnosis.json"

        # Create directory structure
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        self.step_records = []
        self.event_index = 0
        self.redactor = Redactor()
        self.events = []
        self.traces_dir = self.run_dir / "traces"
        self.traces_dir.mkdir(exist_ok=True)
        for channel in ("model", "action", "environment", "artifact", "diagnosis"):
            (self.traces_dir / f"{channel}_trace.jsonl").touch()

    def layer(self, channel, name, **data):
        if channel not in {"model", "action", "environment", "artifact", "diagnosis"}:
            raise ValueError("Unknown trace channel")
        record = self.redactor.clean({"schema_version": "0.2", "run_id": self.run_id,
            "task_id": self.task_id, "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": name, "status": "RECORDED", "latency_ms": None, "error_type": None, **data})
        with (self.traces_dir / f"{channel}_trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def event(self, name, **data):
        self.event_index += 1
        record = self.redactor.clean({"event_seq": self.event_index, "event": name,
                  "timestamp": datetime.now(timezone.utc).isoformat(), **data})
        self.events.append(record)
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            stream.flush()
        channel = ("model" if name.startswith("model_") else
                   "artifact" if name.startswith("artifact_") else
                   "action" if name.startswith(("action_", "primitive_", "structural_", "runtime_verification")) else "environment")
        self.layer(channel, name, **{k: v for k, v in record.items() if k != "event"})

    def save_json(self, name, value):
        target = self.run_dir / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.redactor.clean(value), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(target)

    def record_step(self, step_record: Union[TraceStepRecord, Dict[str, Any]]) -> None:
        """Appends a step record to the in-memory log and trace.jsonl file."""
        if isinstance(step_record, TraceStepRecord):
            record_dict = step_record.model_dump()
        else:
            record_dict = step_record

        # Keep runtime observations absolute while persisted references are portable.
        from copy import deepcopy
        record_dict = deepcopy(record_dict)
        record_dict["schema_version"] = "2.0"
        for key in ("obs_before", "obs_after"):
            obs = record_dict.get(key)
            if obs and obs.get("screenshot_path"):
                shot = Path(obs["screenshot_path"])
                if shot.is_absolute():
                    obs["screenshot_path"] = shot.relative_to(self.run_dir).as_posix()

        record_dict = self.redactor.clean(record_dict)
        self.step_records.append(record_dict)

        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record_dict, ensure_ascii=False) + "\n")
        self.layer("environment", "observation_and_execution", **record_dict)
        if record_dict.get("action"):
            execution = record_dict.get("execution_result") or {}
            self.layer("action", "action_executed", step=record_dict["step"],
                       action=record_dict["action"], lowered_actions=record_dict.get("lowered_actions"),
                       execution_result=execution, error_type=record_dict.get("error_type"),
                       status="FAILED" if record_dict.get("error_type") or execution.get("success") is False else
                              "SUCCEEDED" if execution.get("success") is True else "RECORDED")

    def save_result(self, result: Union[RunSummary, Dict[str, Any]]) -> None:
        """Saves overall execution result to result.json."""
        data = result.model_dump() if isinstance(result, RunSummary) else result
        self.save_json("result.json", data)

    def save_diagnosis(self, diagnosis: Dict[str, Any]) -> None:
        """Saves failure diagnostic report to diagnosis.json."""
        from diagagent.diagnosis.classifier import classify
        diagnosis = {**diagnosis, **classify(self.step_records, self.events, diagnosis)}
        self.save_json("diagnosis.json", diagnosis)
        self.layer("diagnosis", "diagnosis_completed", diagnosis=diagnosis)
