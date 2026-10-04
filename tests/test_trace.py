"""Unit tests for TraceRecorder and TraceReader."""

import json
from pathlib import Path
from diagagent.trace.models import RunSummary, TraceStepRecord
from diagagent.trace.recorder import TraceRecorder
from diagagent.trace.storage import TraceReader


def test_trace_recorder_and_reader(tmp_path):
    recorder = TraceRecorder(run_dir=tmp_path, task_id="test_task", run_id="run_001")
    assert recorder.run_dir.exists()
    assert recorder.screenshots_dir.exists()
    assert recorder.artifacts_dir.exists()

    # Record steps
    recorder.record_step(TraceStepRecord(step=0, phase="reset"))
    recorder.record_step(
        TraceStepRecord(
            step=1,
            phase="step",
            action={"type": "resize_image", "width": 512, "height": 512},
            done=False,
        )
    )

    recorder.save_result(RunSummary(task_id="test_task", run_id="run_001", success=True, total_steps=1))
    recorder.save_diagnosis({"success": True, "first_failure_step": None})

    # Read back
    reader = TraceReader(recorder.run_dir)
    steps = reader.read_steps()
    assert len(steps) == 2
    assert steps[0].step == 0
    assert steps[1].step == 1
    assert steps[1].action["width"] == 512

    result = reader.read_result()
    assert result["success"] is True

    diag = reader.read_diagnosis()
    assert diag["success"] is True
