"""Portable static HTML dashboard; no implicit browser or server process."""
import html
import json
from pathlib import Path
from urllib.parse import quote
from diagagent.trace.paths import evidence_path


def generate_html_report(run_dir, output_html=None):
    root = Path(run_dir).resolve()
    def read(name):
        path = root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    def esc(value):
        return html.escape(str(value), quote=True)
    def pretty(value):
        return esc(json.dumps(value, indent=2, ensure_ascii=False))
    def picture(ref, label):
        path = evidence_path(root, ref)
        if not path:
            return '<p class="unknown">Screenshot/artifact unavailable</p>'
        url = quote(path.relative_to(root).as_posix(), safe="/")
        return f'<a href="{url}"><img src="{url}" alt="{esc(label)}" loading="lazy"></a>'
    result, diagnosis, metadata = read("result.json"), read("diagnosis.json"), read("metadata.json")
    graph = read("evidence_graph.json")
    trace = root / "trace.jsonl"
    steps = []
    parse_errors = []
    if trace.exists():
        for index, line in enumerate(trace.read_text(encoding="utf-8").splitlines(), 1):
            try:
                steps.append(json.loads(line))
            except ValueError:
                parse_errors.append(f"Incomplete/corrupt trace line {index}")
    cards = []
    for row in steps:
        evaluation = row.get("step_eval") or {}
        status = evaluation.get("status") or ("FAIL" if evaluation.get("error_type") else
                 "PASS (legacy)" if evaluation.get("subgoal_completed") is True else "UNKNOWN")
        action = row.get("action") or {"phase": row.get("phase", "unknown")}
        images = ''.join(picture((row.get(key) or {}).get("screenshot_path"), key)
                         for key in ("obs_before", "obs_after") if row.get(key))
        cards.append(f'<section><h2>Step {esc(row.get("step"))} · {esc(action.get("type", row.get("phase")))} · {esc(status)}</h2>'
            f'<div class="images">{images}</div><details><summary>Action, execution and evaluation evidence</summary>'
            f'<pre>{pretty({"action": action, "execution": row.get("execution_result"), "evaluation": evaluation})}</pre></details></section>')
    states = {key: result.get(key, "UNKNOWN") for key in ("task_status", "process_status", "artifact_status", "termination_reason")}
    identity = {key: metadata.get(key, "unavailable") for key in ("backend", "agent_type", "model", "evidence_kind", "source_sha256")}
    metrics = {key: result.get(key) for key in ("duration_seconds", "total_decisions", "executed_primitives", "model_call_count", "model_usage")}
    from diagagent.feedback.view import feedback_form
    correction_form = feedback_form(root)
    recovery_sections = []
    recovery_root = root / "recovery_sessions"
    if recovery_root.exists():
        for session in sorted(p for p in recovery_root.iterdir() if p.is_dir()):
            events_path = session / "recovery_events.jsonl"
            events = []
            if events_path.exists():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
            recovery_sections.append(
                f'<section><h2>Recovery Timeline · {esc(session.name)}</h2>'
                f'<pre>{pretty(events)}</pre>'
                f'<p>Artifacts: {esc(", ".join(p.name for p in session.iterdir() if p.is_file()))}</p></section>'
            )
    recovery_timeline = ''.join(recovery_sections)
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>DiagAgent · {esc(result.get("task_id", root.name))}</title>
<style>body{{font:16px system-ui;background:#f3f5f8;color:#172334;max-width:1150px;margin:auto;padding:24px}}section,header{{background:white;padding:22px;margin:18px 0;border-radius:10px}}h1{{font-size:26px}}h2{{font-size:18px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1f5;padding:14px}}.images{{display:flex;gap:12px}}.images a{{width:50%}}img{{max-width:100%;max-height:480px;object-fit:contain}}summary{{cursor:pointer}}.unknown{{color:#7d5512}}</style>
<header><h1>DiagAgent · {esc(result.get("task_id", root.name))}</h1><p>{esc(diagnosis.get("task_instruction", ""))}</p><pre>{pretty(states)}</pre></header>
<section><h2>Run identity and measurements</h2><pre>{pretty(identity)}</pre><pre>{pretty(metrics)}</pre></section>
<section><h2>Observed failure and evidence</h2><pre>{pretty({key: diagnosis.get(key) for key in ("first_failure_step", "failure_type", "responsibility", "attribution_status", "has_observed_failure", "evidence", "recommendation")})}</pre><p>{esc('; '.join(parse_errors))}</p></section>
<section><h2>AI Diagnosis v0.2 / v0.3</h2><pre>{pretty({key: diagnosis.get(key) for key in ("failure", "step", "type", "layer", "confidence", "confidence_score", "explanation", "diagnosis_status", "v02_attribution")})}</pre></section>
<section><h2>Evidence Graph</h2><pre>{pretty({"nodes": graph.get("nodes", []), "edges": graph.get("edges", [])})}</pre></section>
<section><h2>Final artifact</h2>{picture(result.get("artifact_path"), "Final artifact")}</section>
{''.join(cards)}{recovery_timeline}{correction_form}<footer>Static report with adjacent evidence files. Missing evidence is UNKNOWN. Click images for full size.</footer></html>'''
    target = Path(output_html) if output_html else root / "diagnostic_dashboard.html"
    if target.resolve().parent != root:
        raise ValueError("Place HTML inside the run directory to retain relative evidence links")
    if correction_form:
        from diagagent.feedback.view import FORM_SCRIPT
        (root / "feedback_form.js").write_text(FORM_SCRIPT, encoding="utf-8")
    target.write_text(document, encoding="utf-8")
    return target


def serve_dashboard(runs_dir="runs", port=None, run_dir=None):
    if port is not None:
        print("--port is deprecated: dashboard generates static HTML; no HTTP server is started.")
    root = Path(run_dir).resolve() if run_dir else None
    if root is None:
        candidates = [p.parent for p in Path(runs_dir).resolve().rglob("result.json")]
        if not candidates:
            raise FileNotFoundError("No completed run under runs-dir")
        root = max(candidates, key=lambda p: (p / "result.json").stat().st_mtime)
    path = generate_html_report(root)
    print(f"Static dashboard: {path}")
    return path
