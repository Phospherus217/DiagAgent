"""Controlled offline injection suite on original tasks; labels stay outside diagnosis."""
import json
from pathlib import Path
from diagagent.agent.rule_agent import RuleAgent
from diagagent.agent.runtime import AgentRuntime
from diagagent.core.errors import ModelAPIError
from diagagent.environment.gimp_env import GIMPEnvironment
from diagagent.environment.errors import DesktopError
from diagagent.tasks import load_task


# External ground truth is used only for scoring after the run has finished.
CASES = [
    ("resize_parameter", "resize_image", "Parameter Error", "Agent", 2),
    ("export_missing_path", "resize_image", "Action Format Error", "Agent", 3),
    ("text_wrong_tool", "add_text", "Tool Selection Error", "Agent", 2),
    ("resize_dialog", "resize_image", "Dialog Error", "Environment", 2),
    ("launch_window", "resize_image", "Window Error", "Environment", 0),
    ("api_401", "resize_image", "API Error", "Environment", 2),
    ("resize_noop", "resize_image", "Execution No-op", "Execution", 2),
    ("export_io", "resize_image", "File I/O Error", "Execution", 3),
    ("corrupt_artifact", "resize_image", "Artifact Error", "Artifact", None),
    ("false_completion", "resize_image", "False Completion", "Artifact", 2),
    ("crop_invalid_region", "crop_canvas", "Action Format Error", "Agent", 2),
    ("text_position", "add_text", "Canvas Target Error", "Agent", 3),
    ("filter_parameter", "gaussian_blur", "Parameter Error", "Agent", 2),
]


def run_injections(output, task_root):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for name, task_name, expected, owner, first_step in CASES:
        task = load_task(Path(task_root) / f"{task_name}.yaml")
        env = GIMPEnvironment(run_root=output / name)
        agent = RuleAgent()
        original = agent.act
        def act(obs, original=original, name=name):
            action = original(obs)
            if name == "resize_parameter" and action.type == "resize_image":
                return action.model_copy(update={"width": 128, "height": 128})
            if name == "export_missing_path" and action.type == "export_file":
                return {"type": "export_file"}
            if name == "text_wrong_tool" and action.type == "select_tool":
                return action.model_copy(update={"tool_name": "brush"})
            if name == "false_completion":
                return {"type": "stop", "message": "Complete"}
            if name == "crop_invalid_region" and action.type == "crop_canvas":
                return action.model_copy(update={"region": [100, 100, 20, 20]})
            if name == "text_position" and action.type == "add_text":
                return action.model_copy(update={"position": (25, 50)})
            if name == "filter_parameter" and action.type == "apply_filter":
                return action.model_copy(update={"parameters": {"radius": 1}})
            return action
        agent.act = act
        def fail(kind):
            def injected(*args, **kwargs):
                raise DesktopError("Controlled boundary failure", kind)
            return injected
        model = None
        if name == "resize_dialog":
            env.backend.resize_image = fail("Dialog Operation Error")
        elif name == "launch_window":
            env.backend.launch = fail("Launch / Window Error")
        elif name == "api_401":
            class UnavailableModel:
                def query(self, *args, **kwargs):
                    raise ModelAPIError("Controlled HTTP authentication failure", status_code=401)
            model = UnavailableModel()
        elif name == "resize_noop":
            env.backend.resize_image = lambda *args: None
        elif name == "export_io":
            env.backend.export_file = fail("File I/O Error")
        elif name == "corrupt_artifact":
            env.backend.export_file = lambda path: Path(path).write_bytes(b"not an image")
        AgentRuntime(env, automatic_recovery=False).run_task(task, agent=None if model else agent, model=model)
        root = env.trace_recorder.run_dir
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        env.trace_recorder.save_json("metadata.json", {**metadata, "evidence_kind": "simulated_fault", "agent_type": "fault"})
        diagnosis = json.loads((root / "diagnosis.json").read_text(encoding="utf-8"))
        results.append({"case": name, "run": str(root.relative_to(output)), "expected_type": expected,
            "expected_step": first_step, "expected_responsibility": owner,
            "actual_type": diagnosis["type"], "actual_step": diagnosis["step"],
            "actual_responsibility": diagnosis["layer"], "detected": diagnosis["failure"],
            "first_failure_correct": diagnosis["step"] == first_step,
            "responsibility_correct": diagnosis["layer"] == owner,
            "type_correct": diagnosis["type"] == expected})
    # A clean negative control prevents an always-failure classifier from scoring 100%.
    env = GIMPEnvironment(run_root=output / "clean_control")
    AgentRuntime(env, automatic_recovery=False).run_task(load_task(Path(task_root) / "resize_image.yaml"), RuleAgent())
    clean = json.loads((env.trace_recorder.run_dir / "diagnosis.json").read_text(encoding="utf-8"))
    summary = {"evidence_kind": "simulated_fault", "real_gui_acceptance": False, "cases": results,
        "negative_control": {"run": str(env.trace_recorder.run_dir.relative_to(output)), "false_positive": clean["failure"]},
        "metrics": {"failure_detection_accuracy": (sum(r["detected"] for r in results) + int(not clean["failure"])) / (len(results) + 1),
            "first_failure_accuracy": sum(r["first_failure_correct"] for r in results) / len(results),
            "responsibility_accuracy": sum(r["responsibility_correct"] for r in results) / len(results)},
        "passed": not clean["failure"] and all(r["detected"] and r["first_failure_correct"] and r["responsibility_correct"] and r["type_correct"] for r in results)}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
