"""Serial fixed-plan acceptance with immutable denominators and explicit aborts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from diagagent.environment.gimp_env import GIMPEnvironment

ROOT = Path(__file__).resolve().parents[1]
TASKS = ("export_png", "resize_export")


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def identity(config):
    files = [Path(config), Path(__file__)] + sorted((ROOT / "benchmark/smoke").glob("*.yaml"))
    files += sorted((ROOT / "benchmark/assets").glob("*"))
    return {"source_sha256": GIMPEnvironment.source_fingerprint(),
            "inputs_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}}


def run_child(command, timeout):
    """On interruption, clean up only this child and its descendants."""
    if command[command.index("--backend") + 1] == "mock":
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    import psutil  # desktop extra; imported only for actual batch execution
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except BaseException:
        try:
            parent = psutil.Process(process.pid)
            owned = parent.children(recursive=True) + [parent]
        except psutil.NoSuchProcess:
            owned = []
        for child in reversed(owned):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(owned, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.communicate()
        raise


def run_batch(output, config, repeats=5, backend="real", runner=run_child, fingerprint=identity):
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("repeats must be an integer in [1, 20]")
    if backend not in {"real", "mock"}:
        raise ValueError("backend must be real or mock")
    config, output = Path(config).resolve(), Path(output).resolve()
    version = fingerprint(config)
    output.mkdir(parents=True, exist_ok=False)
    plan = [dict(attempt=i * repeats + j + 1, task=task, status="NOT_STARTED")
            for i, task in enumerate(TASKS) for j in range(repeats)]
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), identity=version,
        backend=backend, planned=len(plan), attempts=plan, state="RUNNING", passed=False,
        boundary="Fixed-layout engineering acceptance; mock batches are offline checks only")
    write(output / "plan.json", manifest)
    try:
        for row in plan:
            if fingerprint(config) != version:
                manifest.update(state="ABORTED", abort_reason="version_changed")
                break
            target = output / f"attempt_{row['attempt']:02d}"
            row["status"] = "STARTED"
            write(output / "plan.json", manifest)
            command = [sys.executable, "-m", "diagagent.cli.main", "run",
                str(ROOT / "benchmark/smoke" / (row["task"] + ".yaml")), "--backend", backend,
                "--config", str(config), "--output-dir", str(target)]
            try:
                completed = runner(command, timeout=240)
                (output / f"attempt_{row['attempt']:02d}.log").write_text(
                    (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
                row["exit_code"] = completed.returncode
                files = list(target.glob("*/result.json"))
                if len(files) != 1:
                    raise ValueError("Expected exactly one result.json")
                result = json.loads(files[0].read_text(encoding="utf-8"))
                metadata = json.loads((files[0].parent / "metadata.json").read_text(encoding="utf-8"))
                valid_identity = (metadata.get("source_sha256") == version["source_sha256"] and
                    metadata.get("backend") == ("real_gimp" if backend == "real" else "mock") and
                    result.get("task_id") == ("da_smoke_export_png" if row["task"] == "export_png" else "da_smoke_resize_export"))
                status = result.get("task_status", "UNKNOWN")
                if not valid_identity or status not in {"PASS", "FAIL", "UNKNOWN"}:
                    status = "UNKNOWN"
                if status == "PASS" and (completed.returncode != 0 or result.get("termination_reason") != "agent_stop"):
                    status = "UNKNOWN"
                row.update(status=status, identity_valid=valid_identity,
                    run=files[0].parent.relative_to(output).as_posix(),
                    artifact_status=result.get("artifact_status", "UNKNOWN"),
                    process_status=result.get("process_status", "UNKNOWN"),
                    termination_reason=result.get("termination_reason", "missing_result"))
            except KeyboardInterrupt:
                row.update(status="UNKNOWN", termination_reason="cancelled")
                manifest.update(state="ABORTED", abort_reason="cancelled")
                break
            except subprocess.TimeoutExpired as error:
                def as_text(value):
                    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                (output / f"attempt_{row['attempt']:02d}.log").write_text(
                    as_text(error.stdout) + as_text(error.stderr), encoding="utf-8")
                row.update(status="UNKNOWN", termination_reason="batch_timeout")
                manifest.update(state="ABORTED", abort_reason="batch_timeout")
                break
            except (OSError, ValueError, TypeError) as error:
                row.update(status="UNKNOWN", termination_reason="batch_error", error=str(error))
                manifest.update(state="ABORTED", abort_reason="batch_error")
                break
            finally:
                write(output / "plan.json", manifest)
            if fingerprint(config) != version:
                row.update(status="UNKNOWN", identity_valid=False)
                manifest.update(state="ABORTED", abort_reason="version_changed_during_attempt")
                break
            print(f"Attempt {row['attempt']}/{len(plan)} {row['task']}: {row['status']}", flush=True)
        else:
            manifest["state"] = "FINISHED"
    except BaseException as error:
        manifest.update(state="ABORTED", abort_reason=type(error).__name__)
        for row in plan:
            if row["status"] == "STARTED":
                row.update(status="UNKNOWN", termination_reason="batch_interrupted")
        raise
    finally:
        groups = {task: dict(planned=repeats, **{
            state.lower(): sum(r["status"] == state for r in plan if r["task"] == task)
            for state in ("PASS", "FAIL", "UNKNOWN", "NOT_STARTED")}) for task in TASKS}
        offline_pass = manifest["state"] == "FINISHED" and all(g["pass"] == repeats for g in groups.values())
        manifest.update(groups=groups, offline_batch_pass=offline_pass if backend == "mock" else None,
            passed=backend == "real" and repeats == 5 and manifest["state"] == "FINISHED"
                   and all(g["pass"] >= 4 for g in groups.values()),
            completed_at=datetime.now(timezone.utc).isoformat())
        write(output / "plan.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--backend", choices=["real", "mock"], default="real")
    args = parser.parse_args()
    result = run_batch(args.output, args.config, args.repeats, args.backend)
    print(json.dumps(result["groups"]))
    return 130 if result.get("abort_reason") == "cancelled" else 0 if result["passed"] or result["offline_batch_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
