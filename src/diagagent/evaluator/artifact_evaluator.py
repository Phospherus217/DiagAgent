"""Strict decoded artifact checks with explicit unavailable evidence."""
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError
from diagagent.evaluator.models import ArtifactEvaluation


def compute_sharpness(img):
    return float(ImageStat.Stat(img.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0])


def compute_mean_diff(a, b):
    if a.size != b.size:
        b = b.resize(a.size)
    return sum(ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean) / 3


class ArtifactEvaluator:
    def __init__(self, task_spec):
        self.task_spec = task_spec

    def evaluate(self, artifact_path):
        result = ArtifactEvaluation()
        criteria = (self.task_spec.get("success_criteria") or {}).get("final") or {}
        path = Path(artifact_path)

        def check(name, status, expected=None, actual=None, method="file_decode", required=True):
            result.checks.append(dict(name=name, status=status, expected=expected, actual=actual,
                method=method, required=required, applicable=True, evidence_refs=["artifacts/" + path.name]))

        def finish():
            statuses = [c["status"] for c in result.checks if c["required"]]
            result.status = "FAIL" if "FAIL" in statuses else "UNKNOWN" if not statuses or "UNKNOWN" in statuses else "PASS"
            result.artifact_pass = result.status == "PASS"
            if result.status == "FAIL":
                result.error_type = "Artifact Error"
            result.note = f"Required artifact checks: {result.status}"
            return result

        try:
            result.output_exists = path.is_file()
            check("exists", "PASS" if result.output_exists else "FAIL", True, result.output_exists, "filesystem")
            if not result.output_exists:
                return finish()
            result.file_nonempty = path.stat().st_size > 0
            check("nonempty", "PASS" if result.file_nonempty else "FAIL", True, result.file_nonempty, "filesystem")
            if not result.file_nonempty:
                return finish()
            try:
                with Image.open(path) as source:
                    source.load()
                    actual_format = (source.format or "").lower()
                    img = source.copy()
                check("decodable", "PASS", True, True)
            except (UnidentifiedImageError, OSError, ValueError):
                check("decodable", "FAIL", True, False)
                return finish()
            result.metrics.update(format=actual_format, size=list(img.size))
            expected_format = str(criteria.get("output_format", "png")).lower()
            expected_format = "jpeg" if expected_format == "jpg" else expected_format
            result.format_correct = actual_format == expected_format
            check("format", "PASS" if result.format_correct else "FAIL", expected_format, actual_format)
            expected_size = criteria.get("output_size")
            if expected_size is None and "width" in criteria and "height" in criteria:
                expected_size = [criteria["width"], criteria["height"]]
            if expected_size:
                result.size_correct = list(img.size) == list(expected_size)
                check("size", "PASS" if result.size_correct else "FAIL", list(expected_size), list(img.size))
            if criteria.get("blur_metric_change"):
                initial = (self.task_spec.get("initial_state") or {}).get("input_file")
                try:
                    with Image.open(initial) as original:
                        original.load()
                        prior = compute_sharpness(original)
                    actual = compute_sharpness(img)
                    result.metrics.update(input_sharpness=prior, output_sharpness=actual)
                    result.content_correct = actual < prior * .95
                    check("blur_metric", "PASS" if result.content_correct else "FAIL", prior * .95, actual, "edge_mean_heuristic")
                except Exception:
                    result.content_correct = False
                    result.error_type = "Evaluator Error"
                    check("blur_metric", "UNKNOWN", method="reference_unavailable")
            unsupported = [sg["id"] for sg in self.task_spec.get("subgoals", [])
                           if (sg.get("expected_artifact_change") or {}).get("text")]
            if unsupported:
                result.content_correct = False
                check("text_semantics", "UNKNOWN", actual=unsupported, method="not_implemented")
        except Exception as error:
            result.error_type = "Evaluator Error"
            result.metrics["evaluation_error"] = type(error).__name__
            check("evaluation", "UNKNOWN", method="evaluator_exception")
        return finish()
