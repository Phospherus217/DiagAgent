"""Task path resolution and explicitly separated public/oracle views."""
from copy import deepcopy
from pathlib import Path
import re
import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def output_name(value):
    value = str(value)
    if not value or value in {".", ".."} or any(c in value for c in '/\\:'):
        raise ValueError("Output must be a filename inside this run's artifacts directory")
    return value


def normalize_task(task, source=None):
    result = deepcopy(task)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(result.get("task_id", ""))):
        raise ValueError("task_id must contain only letters, digits, underscores and hyphens")
    if str(result.get("schema_version")) == "2.0":
        if source is None:
            raise ValueError("Schema 2 task needs its source file for asset resolution")
        asset = (Path(source).parent / result["initial_asset"]).resolve()
        final = result["evaluation"]["final"]
        result["initial_state"] = {"input_file": str(asset), "output_file": output_name(result["output_name"])}
        result["success_criteria"] = {"final": {
            "output_exists": True, "file_nonempty": True,
            "output_format": final["format"], "output_size": final["size"],
        }}
        # Explicit oracle-only reference plan, never included in public_task().
        plan = result.get("reference_actions", [])
        result["subgoals"] = [{"id": "open_image", "order": 1, "type": "ui_state"}]
        for index, action in enumerate(plan, 2):
            if action["type"] == "resize_image":
                result["subgoals"].append({"id": "resize_image", "order": index,
                    "type": "parameter", "expected_state": {"width": action["width"], "height": action["height"]}})
            elif action["type"] == "export_file":
                result["subgoals"].append({"id": "export_png", "order": index, "type": "artifact"})
            else:
                raise ValueError("Day 1 reference plan supports resize_image/export_file only")
    else:
        initial = result.setdefault("initial_state", {})
        asset = Path(initial["input_file"])
        initial["input_file"] = str(asset.resolve() if asset.is_absolute() else (PACKAGE_ROOT / asset).resolve())
        initial["output_file"] = output_name(initial.get("output_file", "output.png"))
    if not Path(result["initial_state"]["input_file"]).is_file():
        raise FileNotFoundError(result["initial_state"]["input_file"])
    if not isinstance(result.get("max_steps", 8), int) or result.get("max_steps", 8) <= 0:
        raise ValueError("max_steps must be a positive integer")
    result["_normalized"] = True
    return result


def load_task(path):
    source = Path(path).resolve()
    return normalize_task(yaml.safe_load(source.read_text(encoding="utf-8")), source)


def public_task(task):
    return {key: deepcopy(task[key]) for key in (
        "task_id", "instruction", "task_family", "difficulty", "allowed_actions",
        "allowed_observations", "max_steps", "output_name") if key in task}
