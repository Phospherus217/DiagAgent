"""Validate an installed standalone source copy using a supplied clean interpreter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone
from build_public_release import export_public


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root):
        raise ValueError("Validation outputs must stay inside DiagAgent")
    output.mkdir(parents=True, exist_ok=False)
    copy = output / "standalone"
    export_public(root, copy)
    work = output / "unrelated_workdir"
    work.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    executable = str(args.python.resolve())
    commands = [
        [executable, "-I", "-m", "pip", "install", "-e", str(copy), "--no-deps", "--no-build-isolation", "--disable-pip-version-check"],
        [executable, "-I", "-m", "pip", "check"],
        [executable, "-I", "-c", "import diagagent, pathlib; print(pathlib.Path(diagagent.__file__).resolve())"],
        [executable, "-I", "-m", "pytest", str(copy / "tests"), "-c", str(copy / "pyproject.toml"), "-q", "-p", "no:cacheprovider", "-m", "not desktop and not model"],
        [executable, "-I", "-m", "diagagent.cli.main", "run", str(copy / "benchmark/smoke/resize_export.yaml"), "--backend", "mock", "--output-dir", str(output / "mock_runs")],
        [executable, "-I", "-m", "diagagent.experiments.recovery_cases", "--case", "MATCHED-PARAMETER", "--mode", "constrained_recovery", "--output", str(output / "recovery_fixture")],
        [executable, "-I", "-m", "pip", "freeze", "--exclude-editable"],
    ]
    records = []
    for index, command in enumerate(commands, 1):
        result = subprocess.run(command, cwd=work, env=env, text=True, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=240)
        log = output / f"check_{index:02d}.log"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        passed = result.returncode == 0
        if index == 3:
            passed = passed and result.stdout.strip().startswith(str(copy / "src"))
        records.append(dict(check=index, exit_code=result.returncode, passed=passed, log=log.name))
        print(f"Standalone check {index}: {'PASS' if passed else 'FAIL'}", flush=True)
        if not passed:
            break
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), passed=len(records) == len(commands) and all(r["passed"] for r in records),
        checks=records, mode="isolated interpreter, installed standalone copy, unrelated cwd, mock only",
        copied_source_sha256={p.relative_to(copy).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (copy / "src").rglob("*.py")})
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
