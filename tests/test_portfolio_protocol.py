import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from diagagent.experiments import portfolio_protocol as p


def test_selected_policy_action_replaces_target_and_keeps_export_prefix():
    task = p.load_task(p.TASK)
    agent = p.SequenceAgent(repair_action={"type": "resize_image", "width": 384, "height": 384})
    agent.reset(task)
    assert agent.act(None).width == 384
    assert agent.act(None).type == "export_file"
    agent = p.SequenceAgent(repair_action={"type": "export_file", "path": "output.png", "format": "png"})
    agent.reset(task)
    assert agent.act(None).type == "resize_image"
    assert agent.act(None).type == "export_file"
    assert agent.changed


def test_registration_cannot_be_overwritten(tmp_path):
    p.dump(tmp_path / "protocol.json", {"status": "frozen"})
    with pytest.raises(FileExistsError):
        p.dump(tmp_path / "protocol.json", {"status": "changed"})


def test_admission_is_independent_of_predicted_diagnosis(tmp_path):
    (tmp_path / "trace.jsonl").write_text(json.dumps({"action": {"type": "resize_image", "width": 256},
        "obs_after": {"ui_state": {"image_size": [256, 256]}}}) + "\n", encoding="utf-8")
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    assert p.initial_failure_observed("parameter", {"run_dir": str(tmp_path), "report": {"failure_type": "Wrong"}})
    assert not p.initial_failure_observed("dialog", {"run_dir": str(tmp_path)})


def test_dialog_fault_dispatches_escape_instead_of_raising(monkeypatch):
    env = object.__new__(p.FaultEnvironment)
    env.fault, env.injected = "dialog", False
    env.backend = SimpleNamespace(windows=lambda: [SimpleNamespace(title="Scale Image", left=0, width=400, bottom=500)],
                                  current_image_size=lambda: [800, 600])
    events, actions = [], []
    env._event = lambda name, **kw: events.append((name, kw))
    monkeypatch.setattr(p.GIMPEnvironment, "_execute_primitive",
                        lambda self, a: actions.append(a) or SimpleNamespace(success=True))
    env._execute_primitive({"type": "click", "x": 256, "y": 478})
    assert actions == [{"type": "hotkey", "keys": "esc"}]
    assert env.injected and events[-1][0] == "fault_injection_reached"


def recorded_run(tmp_path):
    task = p.load_task(p.TASK)
    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    p.dump(root / "result.json", {"success": True, "duration_seconds": 1.25})
    p.dump(root / "evaluation.json", {"steps": [{"subgoal_id": s["id"], "status": "PASS"}
             for s in task["subgoals"]], "artifact": {"status": "PASS"}})
    actions = [{"type": "resize_image", "width": 512, "height": 512},
               {"type": "export_file", "path": "output.png", "format": "png"}]
    rows = [{"phase": "reset"}] + [{"action": a} for a in actions]
    (root / "trace.jsonl").write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    events = [{"event": "primitive_started", "action": {"type": "type", "text": str(root / "artifacts/output.png")}}]
    (root / "events.jsonl").write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
    with Image.open(task["initial_state"]["input_file"]) as image:
        image.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS).save(root / "artifacts/output.png")
    return root, task, actions


def test_verification_counts_actions_and_compares_pixels(tmp_path):
    root, task, actions = recorded_run(tmp_path)
    measured = p.inspect_run(root, task, p.sha(task["initial_state"]["input_file"]), actions[0])
    assert measured["process_recovered"]
    assert measured["extra_semantic_actions"] == 3
    assert measured["extra_primitive_actions"] == 1
    assert all(measured["side_effect_checks"].values())
    assert measured["measurements"]["reference_rgb_mae"] == 0
    assert measured["policy_action_match_count"] == 1


def test_plausible_size_with_wrong_content_cannot_pass_side_effects(tmp_path):
    root, task, actions = recorded_run(tmp_path)
    Image.new("RGB", (512, 512), "black").save(root / "artifacts/output.png")
    measured = p.inspect_run(root, task, p.sha(task["initial_state"]["input_file"]), actions[0])
    assert measured["artifact_recovered"]  # persisted dimensions check alone was PASS
    assert measured["side_effect_checks"]["unexpected_crop"] is False
    assert measured["measurements"]["reference_rgb_mae"] > 8


def test_driver_cannot_claim_an_unexecuted_guarded_action(tmp_path):
    root, task, _ = recorded_run(tmp_path)
    measured = p.inspect_run(root, task, p.sha(task["initial_state"]["input_file"]),
        {"type": "resize_image", "width": 384, "height": 384})
    assert not measured["checks"]["policy_action_executed"]


def test_failed_and_unknown_required_process_steps_stay_in_denominator(tmp_path):
    root, task, actions = recorded_run(tmp_path)
    path = root / "evaluation.json"
    data = p.read(path)
    data["steps"][1]["status"] = "UNKNOWN"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not p.inspect_run(root, task, p.sha(task["initial_state"]["input_file"]), actions[0])["process_recovered"]
