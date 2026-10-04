"""Bounded healing orchestration with a safe dry-run path."""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union

from diagagent.healing.guard import GuardViolation, HealingGuard
from diagagent.healing.manager import RecoveryManager
from diagagent.healing.policy import RecoveryPolicy
from diagagent.healing.verifier import RecoveryVerifier
from diagagent.schemas.recovery import (
    HealingRunResult, HealingState, RecoveryPolicyInput, RecoverySession,
)


class HealingLoop:
    """Coordinates diagnosis, policy and verification without owning GUI I/O."""

    def __init__(self, *, policy: Optional[RecoveryPolicy] = None, guard: Optional[HealingGuard] = None):
        self.policy = policy or RecoveryPolicy()
        self.guard = guard or HealingGuard(max_recovery_attempts=1)

    def dry_run(self, *, run_context: Dict[str, Any], diagnosis: Any,
                evidence_graph: Dict[str, Any], task_spec: Dict[str, Any],
                feedback: Any = None, run_dir: Optional[Union[str, Path]] = None) -> HealingRunResult:
        run_id = str(run_context.get("run_id", ""))
        task_id = str(run_context.get("task_id", ""))
        step = diagnosis.first_failure_step if hasattr(diagnosis, "first_failure_step") else diagnosis.get("first_failure_step")
        reason = diagnosis.failure_type if hasattr(diagnosis, "failure_type") else diagnosis.get("failure_type")
        manager = RecoveryManager(run_dir)
        session = manager.start_session(run_id=run_id, task_id=task_id, trigger_step=step,
                                        trigger_reason=reason or "failure_observed")
        manager.attach_diagnosis(diagnosis, evidence_graph)
        decision = self.policy.decide(RecoveryPolicyInput(
            diagnosis=diagnosis, evidence_graph=evidence_graph, task_spec=task_spec,
            current_state=run_context.get("current_state", {}), feedback=feedback,
        ))
        manager.attach_policy(decision)
        # The guard is evaluated even in dry-run mode.  This preserves the
        # same execution boundary used by a future explicit executor while
        # ensuring that an unsafe proposal is surfaced as a denial rather than
        # appearing executable in an audit package.
        guard_error = None
        if decision.repairability.value == "REPAIRABLE":
            try:
                self.guard.validate(
                    diagnosis=diagnosis, evidence_graph=evidence_graph,
                    decision=decision, task_spec=task_spec,
                    current_state=run_context.get("current_state", {}),
                    existing_attempts=0,
                )
                manager.record_guard({"decision": "ALLOW", "code": "ALLOW", "reason": "Guard checks passed."})
            except Exception as error:
                guard_error = str(error)
                manager.record_guard({"decision": "DENY", "code": getattr(error, "code", "INVALID_ACTION"),
                                      "reason": guard_error}, stop_on_deny=False)
                if manager.session_dir:
                    (manager.session_dir / "guard_rejection.json").write_text(
                        json.dumps({"schema_version": "1.0", "executed": False,
                                    "error": guard_error}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        # Dry-run stops before execution. A proposed plan with no approval is
        # represented as AWAITING_APPROVAL/ESCALATED by the manager.
        if manager.session_dir:
            (manager.session_dir / "dry_run.json").write_text(json.dumps({
                "schema_version": "1.0", "executed": False,
                "decision": decision.model_dump(), "guard_rejection": guard_error,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        return HealingRunResult(
            recovery_session_id=session.recovery_session_id, state=session.state,
            executed=False, recovery_success=None,
            reason=guard_error or decision.repairability_reason,
            session_path=str(manager.session_dir) if manager.session_dir else None,
            outcome="DRY_RUN_PLANNED" if guard_error is None else "GUARD_REJECTED",
        )

    def run(self, *, run_context: Dict[str, Any], executor: Any,
            diagnosis: Any, evidence_graph: Dict[str, Any], task_spec: Dict[str, Any],
            feedback: Any = None, run_dir: Optional[Union[str, Path]] = None,
            max_recovery_attempts: int = 1,
            require_human_approval: bool = False) -> HealingRunResult:
        """Execute exactly one guarded recovery attempt.

        ``executor`` is intentionally injected.  It may be a callable or an
        object exposing ``execute(action)`` and must return independent process,
        artifact and task verification fields.  No retry is performed here.
        """
        if max_recovery_attempts != 1:
            raise ValueError("v1.0 permits exactly one recovery attempt per session")
        run_id = str(run_context.get("run_id", "")); task_id = str(run_context.get("task_id", ""))
        step = diagnosis.first_failure_step if hasattr(diagnosis, "first_failure_step") else diagnosis.get("first_failure_step")
        reason = diagnosis.failure_type if hasattr(diagnosis, "failure_type") else diagnosis.get("failure_type")
        manager = RecoveryManager(run_dir)
        session = manager.start_session(run_id=run_id, task_id=task_id, trigger_step=step,
                                        trigger_reason=reason or "failure_observed")
        manager.attach_diagnosis(diagnosis, evidence_graph)
        decision = self.policy.decide(RecoveryPolicyInput(
            diagnosis=diagnosis, evidence_graph=evidence_graph, task_spec=task_spec,
            current_state=run_context.get("current_state", {}), feedback=feedback,
        ))
        if require_human_approval and not decision.requires_human_approval:
            decision = decision.model_copy(update={"requires_human_approval": True})
        manager.attach_policy(decision)
        if decision.repairability.value != "REPAIRABLE":
            return HealingRunResult(recovery_session_id=session.recovery_session_id,
                state=session.state, executed=False, recovery_success=False,
                reason=decision.repairability_reason, session_path=str(manager.session_dir) if manager.session_dir else None,
                outcome="RECOVERY_DENIED")
        approved = run_context.get("human_approved")
        if decision.requires_human_approval:
            if approved is not True:
                return HealingRunResult(recovery_session_id=session.recovery_session_id,
                    state=session.state, executed=False, recovery_success=None,
                    reason="Human approval is required before execution.",
                    session_path=str(manager.session_dir) if manager.session_dir else None,
                    outcome="AWAITING_APPROVAL")
            manager.approve(True)
        try:
            existing_attempts = 0
            if run_dir:
                root = Path(run_dir)
                existing_attempts = len(list((root / "recovery_sessions").glob("*/attempts/*/action/repair_action.json")))
                if (root / "recovery_attempt.claim.json").exists():
                    existing_attempts = max(1, existing_attempts)
            self.guard.validate(diagnosis=diagnosis, evidence_graph=evidence_graph,
                                decision=decision, task_spec=task_spec,
                                current_state=run_context.get("current_state", {}),
                                existing_attempts=existing_attempts, human_approved=approved)
            if run_dir:
                # Exclusive persistent reservation prevents a second session
                # or concurrent process from resetting the original run budget.
                try:
                    with (Path(run_dir) / "recovery_attempt.claim.json").open("x", encoding="utf-8") as handle:
                        json.dump({"run_id": run_id, "recovery_session_id": session.recovery_session_id,
                                   "max_attempts": 1}, handle)
                except FileExistsError:
                    raise GuardViolation("A recovery attempt is already reserved for this run.", "BUDGET_EXCEEDED")
        except Exception as error:
            manager.record_guard({"decision": "DENY", "code": getattr(error, "code", "INVALID_ACTION"),
                                  "reason": str(error)})
            if manager.session_dir:
                (manager.session_dir / "guard_rejection.json").write_text(
                    json.dumps({"schema_version": "1.0", "error": str(error)}, ensure_ascii=False, indent=2), encoding="utf-8")
            return HealingRunResult(recovery_session_id=session.recovery_session_id,
                state=session.state, executed=False, recovery_success=False, reason=str(error),
                session_path=str(manager.session_dir) if manager.session_dir else None,
                outcome="GUARD_REJECTED")
        manager.record_guard({"decision": "ALLOW", "code": "ALLOW", "reason": "Guard checks and budget reservation passed."})
        attempt_id = f"attempt_{uuid.uuid4().hex}"
        manager.attach_attempt(attempt_id)
        attempt_dir = manager.session_dir / "attempts" / attempt_id if manager.session_dir else None
        if attempt_dir:
            (attempt_dir / "action").mkdir(parents=True, exist_ok=True)
            (attempt_dir / "action" / "repair_action.json").write_text(
                json.dumps(decision.repair_action, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            raw = executor(decision.repair_action) if callable(executor) else executor.execute(decision.repair_action)
            attempt = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw or {})
        except Exception as error:
            attempt = {"process_recovered": False, "artifact_recovered": None,
                       "task_recovered": False, "error": str(error), "evidence_refs": []}
        manager.record_execution(attempt)
        verification = RecoveryVerifier().verify(session=session, attempt=attempt,
                                                 contract=decision.verification_contract)
        if attempt_dir:
            verification_dir = attempt_dir / "verification"; verification_dir.mkdir()
            (verification_dir / "recovery_verification.json").write_text(
                json.dumps(verification.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
            (attempt_dir / "result.json").write_text(json.dumps({"attempt_id": attempt_id,
                "executed": True, "attempt": attempt}, ensure_ascii=False, indent=2), encoding="utf-8")
        manager.attach_verification(verification)
        return HealingRunResult(recovery_session_id=session.recovery_session_id,
            state=session.state, executed=True, recovery_success=verification.recovered,
            reason=verification.reason, session_path=str(manager.session_dir) if manager.session_dir else None,
            outcome="RECOVERED" if verification.recovered else "NOT_RECOVERED")
