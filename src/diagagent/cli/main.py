"""Main Click CLI entry point for DiagAgent."""

import sys
import click

from diagagent.cli.benchmark import run_cli_benchmark
from diagagent.cli.dashboard import serve_dashboard
from diagagent.cli.diagnose import diagnose_cli_run
from diagagent.cli.run import run_cli_task


@click.group()
@click.version_option(version="1.0.0", prog_name="diagagent")
def main():
    """DiagAgent: Failure-Aware Runtime and Evaluation Framework for Professional GUI Agents."""
    pass


@main.command(name="run")
@click.argument("task_file", type=click.Path(exists=True))
@click.option("--agent", default="rule", help="Agent type: 'rule' (oracle baseline), 'mock_fault' (fault injector), or 'model'.")
@click.option("--model", default=None, help="Model adapter: 'mock' or 'openai_compat'.")
@click.option("--fault-type", default="parameter", help="Fault type for mock_fault: 'parameter', 'tool', 'canvas_target', 'format', 'premature_stop'.")
@click.option("--fault-step", default=3, type=int, help="Step index at which to inject failure.")
@click.option("--backend", default="mock", help="Environment backend: 'mock' (headless PIL) or 'real' (Windows desktop).")
@click.option("--output-dir", default="runs", help="Output directory for traces and artifacts.")
@click.option("--config", "config_file", type=click.Path(exists=True), default=None)
@click.option("--no-automatic-recovery", is_flag=True, help="Preserve the first failed attempt without automatic format repair or recovery.")
def run_command(task_file, agent, model, fault_type, fault_step, backend, output_dir, config_file, no_automatic_recovery):
    """Executes a benchmark task and prints the diagnostic execution report."""
    try:
        report = run_cli_task(
            task_file=task_file,
            agent_type=agent,
            fault_type=fault_type,
            fault_step=fault_step,
            backend=backend,
            output_dir=output_dir,
            config_file=config_file,
            model_type=model,
            automatic_recovery=not no_automatic_recovery,
        )
        if getattr(report, "termination_reason", None) == "cancelled":
            raise SystemExit(130)
        if not report.success:
            raise SystemExit(2 if report.task_status == "UNKNOWN" or report.failure_type in {"Environment Error", "Launch / Window Error", "Evaluator Error"} else 1)
    except Exception as e:
        click.secho(f"Error executing task: {e}", fg="red", err=True)
        sys.exit(2)


@main.command(name="diagnose")
@click.argument("run_dir", type=click.Path(exists=True))
@click.option("--recompute", is_flag=True, help="Write a versioned diagnosis without replacing original evidence.")
@click.option("--diagnostic-loop", is_flag=True, help="Write detailed-design first_failure.json and diagnosis JSON/Markdown from stored evidence.")
def diagnose_command(run_dir, recompute, diagnostic_loop):
    """Performs offline failure diagnosis on an existing execution trace."""
    try:
        if diagnostic_loop:
            if recompute:
                raise click.UsageError("Choose either --diagnostic-loop or --recompute")
            from diagagent.diagnosis.report import write_diagnostic_report
            click.echo(write_diagnostic_report(run_dir))
        else:
            diagnose_cli_run(run_dir, recompute=recompute)
    except Exception as e:
        click.secho(f"Error diagnosing run: {e}", fg="red", err=True)
        sys.exit(1)


@main.command(name="benchmark")
@click.option("--tasks-dir", default="benchmark/tasks", help="Directory containing benchmark task YAML specifications.")
@click.option("--backend", default="mock", help="Environment backend: 'mock' or 'real'.")
@click.option("--output-dir", default="runs/benchmark", help="Output directory for benchmark traces.")
def benchmark_command(tasks_dir, backend, output_dir):
    """Runs all benchmark tasks in the suite and computes aggregate reliability metrics."""
    try:
        run_cli_benchmark(tasks_dir=tasks_dir, backend=backend, output_dir=output_dir)
    except Exception as e:
        click.secho(f"Error running benchmark: {e}", fg="red", err=True)
        sys.exit(1)


@main.command(name="dashboard")
@click.option("--runs-dir", default="runs", help="Directory containing execution runs.")
@click.option("--port", default=None, type=int, help="Deprecated; static HTML is generated.")
@click.option("--run-dir", default=None, type=click.Path(exists=True))
def dashboard_command(runs_dir, port, run_dir):
    """Launches the visual diagnostic dashboard for browsing run traces and screenshots."""
    try:
        serve_dashboard(runs_dir=runs_dir, port=port, run_dir=run_dir)
    except Exception as e:
        click.secho(f"Error starting dashboard: {e}", fg="red", err=True)
        sys.exit(1)


@main.command(name="doctor")
@click.option("--config", "config_file", type=click.Path(exists=True), default=None, help="Path to custom desktop config YAML.")
@click.option("--desktop", is_flag=True, help="Probe interactive Windows desktop and capture probe screenshot.")
@click.option("--gimp-input", is_flag=True, help="Safe probe verifying GIMP foreground activation, input, and state change.")
@click.option("--output-dir", default="runs/doctor", help="Output directory for doctor screenshots and logs.")
def doctor_command(config_file, desktop, gimp_input, output_dir):
    """Diagnose Windows desktop environment, GIMP readiness, and capture capabilities."""
    from diagagent.cli.doctor import run_doctor
    try:
        result = run_doctor(
            config_file=config_file,
            desktop_probe=desktop,
            gimp_input_probe=gimp_input,
            output_dir=output_dir,
        )
        if result.get("status") in ("FAIL", "BLOCKED_ENVIRONMENT", "EXECUTION_NOOP"):
            sys.exit(2)
    except Exception as e:
        click.secho(f"Doctor error: {e}", fg="red", err=True)
        sys.exit(2)


@main.command(name="feedback")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--file", "feedback_file", required=True, type=click.Path(exists=True, dir_okay=False))
def feedback_command(run_dir, feedback_file):
    """Import explicit human feedback downloaded from the dashboard."""
    import json
    from pathlib import Path
    from diagagent.feedback.store import save_feedback
    try:
        click.echo(save_feedback(run_dir, json.loads(Path(feedback_file).read_text(encoding="utf-8"))))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="repair-plan")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--feedback", required=True, type=click.Path(exists=True, dir_okay=False))
def repair_plan_command(run_dir, feedback):
    """Create a reviewable plan from a diagnosis and human correction."""
    from diagagent.repair.planner import create_plan
    try:
        click.echo(create_plan(run_dir, feedback))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="repair-run")
@click.argument("plan_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", default="runs/repairs")
def repair_run_command(plan_file, output_dir):
    """Execute one explicit repair attempt on the original backend, with no retry."""
    from diagagent.repair.runner import execute_plan
    try:
        root, report = execute_plan(plan_file, output_dir)
        click.echo(report.format_cli())
        click.echo(f"Repair run: {root}")
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    if not report.success:
        raise SystemExit(1)


@main.command(name="repair")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--feedback", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", default="runs/repairs")
def repair_command(run_dir, feedback, output_dir):
    """Create and explicitly execute one reviewed repair attempt."""
    from diagagent.repair.planner import create_plan
    from diagagent.repair.runner import execute_plan
    try:
        plan = create_plan(run_dir, feedback)
        if __import__("json").loads(plan.read_text(encoding="utf-8")).get("status") != "READY":
            raise click.ClickException("Repair plan needs re-planning; no attempt was executed")
        root, report = execute_plan(plan, output_dir)
        click.echo(f"Repair run: {root}")
        click.echo(report.format_cli())
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    if not report.success:
        raise SystemExit(1)


@main.command(name="metrics")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
def metrics_command(run_dir):
    """Print diagnosis and repair metrics from append-only review records."""
    import json
    from diagagent.evaluation.repair_metric import metrics_from_run
    click.echo(json.dumps(metrics_from_run(run_dir), ensure_ascii=False, indent=2))


@main.command(name="diagnostic-benchmark")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", default="experiments/evaluation_results/diagnosis_benchmark.json")
def diagnostic_benchmark_command(manifest, output):
    """Evaluate Outcome Only, Trace Only and DiagAgent baselines."""
    from diagagent.evaluation.benchmark import run_benchmark
    try:
        click.echo(run_benchmark(manifest, output))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="real-repair-prepare")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", default="experiments/real_repair/runs")
@click.option("--execute", is_flag=True, help="Requires an explicit real-GIMP executor in Python; CLI preparation remains dry-run.")
def real_repair_prepare_command(manifest, output, execute):
    """Validate and materialize the real-GIMP case manifest."""
    from diagagent.experiments.real_repair import run_real_repair_manifest
    if execute:
        raise click.ClickException("CLI cannot invent a real-GIMP executor; use the Python API with execute=True")
    try:
        click.echo(run_real_repair_manifest(manifest, output, execute=False))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="heal")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Diagnose and plan one bounded recovery without GUI execution.")
def heal_command(run_dir, dry_run):
    """Run the v1.0 evidence-grounded healing pipeline for an existing run."""
    if not dry_run:
        raise click.UsageError("v1.0 currently exposes only the explicit --dry-run path")
    from diagagent.healing.service import heal_run_dry_run
    try:
        result = heal_run_dry_run(run_dir)
        payload = result.model_dump()
        # The CLI is a review surface: expose the persisted diagnosis and
        # policy alongside the compact run result without adding new evidence.
        if result.session_path:
            from pathlib import Path
            import json
            session_root = Path(result.session_path)
            for name, key in (("diagnosis.json", "diagnosis"), ("repair_policy.json", "repair_policy"),
                              ("dry_run.json", "dry_run")):
                path = session_root / name
                if path.exists():
                    payload[key] = json.loads(path.read_text(encoding="utf-8"))
        click.echo(__import__("json").dumps(payload, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="recovery-metrics")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", default=None, type=click.Path(file_okay=False),
              help="Directory for recovery_metrics.json/csv and recovery_report.md.")
def recovery_metrics_command(manifest, output_dir):
    """Compute v1.0 recovery metrics from a frozen experiment manifest."""
    from diagagent.metrics.recovery import write_recovery_report
    try:
        paths = write_recovery_report(manifest, output_dir)
        click.echo(__import__("json").dumps({key: str(value) for key, value in paths.items()},
                                             ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="self-healing-benchmark")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", default="experiments/evaluation_results/self_healing_benchmark.json")
def self_healing_benchmark_command(manifest, output):
    """Account for B0 No Repair, B1 Naive Retry and B2 DiagAgent Healing."""
    from diagagent.experiments.self_healing_benchmark import run_self_healing_benchmark
    try:
        click.echo(run_self_healing_benchmark(manifest, output))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


@main.command(name="real-recovery-reference")
@click.argument("case_id")
@click.option("--manifest", default="experiments/real_repair/cases.yaml", type=click.Path(exists=True, dir_okay=False))
@click.option("--executor", type=click.Choice(["pyautogui"]), default="pyautogui", show_default=True)
@click.option("--output", default="experiments/real_repair/reference_runs")
@click.option("--execute", is_flag=True, help="Explicitly execute one real-GIMP reference attempt. Default is dry-run.")
def real_recovery_reference_command(case_id, manifest, executor, output, execute):
    """Run one reference recovery gate; real GUI execution requires --execute."""
    from diagagent.experiments.reference_recovery import run_reference_manifest
    try:
        paths = run_reference_manifest(manifest, output, execute=execute, case_id=case_id)
        for path in paths:
            click.echo(path)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
