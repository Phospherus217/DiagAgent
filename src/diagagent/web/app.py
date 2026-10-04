"""Small optional FastAPI read/review surface.

The core runtime remains filesystem based. This adapter exposes persisted
diagnosis, graph and append-only feedback records when FastAPI is installed;
it never executes a repair implicitly.
"""

from pathlib import Path
from typing import Any, Dict


def create_app(runs_root=None):
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as error:
        raise RuntimeError("FastAPI is optional; install diagagent[web] to use the web adapter") from error

    import os
    root = Path(runs_root or os.getenv("DIAGAGENT_RUNS_ROOT", "runs")).resolve()
    app = FastAPI(title="DiagAgent v0.4", version="0.4.0")

    def read_json(path: Path) -> Dict[str, Any]:
        import json
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Evidence not found: {path.name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Invalid evidence JSON") from error
        return value if isinstance(value, dict) else {"value": value}

    def run_path(run_id: str) -> Path:
        candidate = (root / run_id).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_dir():
            raise HTTPException(status_code=404, detail="Run not found")
        return candidate

    @app.get("/runs/{run_id}")
    def run(run_id: str):
        path = run_path(run_id)
        return {"run_id": run_id, "metadata": read_json(path / "metadata.json"),
                "result": read_json(path / "result.json"),
                "diagnosis": read_json(path / "diagnosis.json"),
                "evidence_graph": read_json(path / "evidence_graph.json") if (path / "evidence_graph.json").exists() else {}}

    @app.get("/runs/{run_id}/feedback")
    def feedback(run_id: str):
        path = run_path(run_id) / "feedback"
        if not path.exists():
            return []
        return [read_json(item) for item in sorted(path.rglob("*.json"))]

    @app.post("/runs/{run_id}/repair-plan")
    def repair_plan(run_id: str, payload: Dict[str, Any]):
        # The web layer only records the request. Explicit CLI execution remains
        # the approval boundary and prevents a hidden retry.
        path = run_path(run_id) / "feedback" / "web_requests"
        path.mkdir(parents=True, exist_ok=True)
        import json
        target = path / f"request_{len(list(path.glob('request_*.json')))+1:04d}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "RECORDED", "request": str(target.relative_to(root))}

    return app


__all__ = ["create_app"]
