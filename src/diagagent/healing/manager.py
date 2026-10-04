"""Recovery session orchestration without GUI execution."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from diagagent.healing.events import RecoveryEventLog
from diagagent.healing.state import HealingStateMachine
from diagagent.schemas.recovery import (
    HealingState, RecoveryPolicyDecision, RecoverySession, RecoveryVerificationResult,
)


class RecoveryManager:
    """Owns session state and audit files; delegates execution and evaluation."""

    def __init__(self, run_dir: Optional[Union[str, Path]] = None):
        self.run_dir = Path(run_dir).resolve() if run_dir else None
        self.session: Optional[RecoverySession] = None
        self.machine: Optional[HealingStateMachine] = None
        self.event_log: Optional[RecoveryEventLog] = None
        self.session_dir: Optional[Path] = None

    def start_session(self, *, run_id: str, task_id: str, trigger_step: Optional[int],
                      trigger_reason: str, run_dir: Optional[Union[str, Path]] = None) -> RecoverySession:
        if self.session is not None:
            raise ValueError("A RecoveryManager can own only one active session")
        root = Path(run_dir).resolve() if run_dir else self.run_dir
        self.run_dir = root
        session = RecoverySession(run_id=run_id, task_id=task_id, trigger_step=trigger_step,
                                  trigger_reason=trigger_reason)
        self.session = session
        self.machine = HealingStateMachine(session.state)
        if root:
            self.session_dir = root / "recovery_sessions" / session.recovery_session_id
            self.session_dir.mkdir(parents=True, exist_ok=False)
            self.event_log = RecoveryEventLog(self.session_dir / "recovery_events.jsonl")
            self._save_session()
        return session

    def _save_session(self):
        if self.session_dir and self.session:
            (self.session_dir / "session.json").write_text(
                json.dumps(self.session.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def _transition(self, target: HealingState, event_type: str, payload_ref: str = "", payload: Optional[Dict[str, Any]] = None):
        assert self.session is not None and self.machine is not None
        source = self.machine.state
        self.machine.transition(target)
        self.session.state = target
        if self.event_log:
            self.event_log.append(recovery_session_id=self.session.recovery_session_id,
                                  state_from=source.value, state_to=target.value,
                                  event_type=event_type, payload_ref=payload_ref, payload=payload)
        self._save_session()

    def attach_diagnosis(self, diagnosis: Any, evidence_graph: Optional[Dict[str, Any]] = None):
        assert self.session is not None
        data = diagnosis.model_dump() if hasattr(diagnosis, "model_dump") else dict(diagnosis)
        self._transition(HealingState.DIAGNOSING, "diagnosis_started")
        self.session.diagnosis_id = str(data.get("diagnosis_id", ""))
        self.session.diagnosis = data
        self.session.failure_type = data.get("failure_type") or data.get("type")
        self.session.first_failure_step = data.get("first_failure_step") if data.get("first_failure_step") is not None else data.get("step")
        self.session.diagnosis_confidence = float(data.get("confidence_score", 0.0) or 0.0)
        self._transition(HealingState.DIAGNOSED, "diagnosis_attached", "diagnosis.json")
        if self.session_dir:
            (self.session_dir / "diagnosis.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            if evidence_graph is not None:
                (self.session_dir / "evidence_graph.json").write_text(json.dumps(evidence_graph, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.session

    def attach_policy(self, decision: RecoveryPolicyDecision):
        assert self.session is not None
        self._transition(HealingState.RECOVERY_DECISION, "recovery_decision")
        self.session.repairability = decision.repairability
        self.session.repairability_reason = decision.repairability_reason
        self.session.repair_plan_id = decision.plan_id
        self.session.proposed_repair = decision.repair_action
        self.session.requires_human_approval = decision.requires_human_approval
        if self.session_dir:
            (self.session_dir / "repair_policy.json").write_text(
                json.dumps(decision.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if decision.repairability.value != "REPAIRABLE":
            self.session.recovery_success = False
            self.session.recovery_reason = decision.repairability_reason
            self._transition(HealingState.ESCALATED, "recovery_denied", "repair_policy.json")
            if self.session_dir:
                (self.session_dir / "final_result.json").write_text(json.dumps({
                    "schema_version": "1.0", "recovery_session_id": self.session.recovery_session_id,
                    "state": self.session.state.value, "recovery_success": False,
                    "reason": decision.repairability_reason,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.session
        self._transition(HealingState.REPAIR_PLANNED, "repair_plan_created", "repair_policy.json")
        if decision.requires_human_approval:
            self._transition(HealingState.AWAITING_APPROVAL, "approval_required")
        return self.session

    def approve(self, approved: bool):
        assert self.session is not None
        if self.machine.state != HealingState.AWAITING_APPROVAL:
            raise ValueError("Session is not awaiting approval")
        self.session.approved_by_human = bool(approved)
        if approved:
            self._transition(HealingState.REPAIR_EXECUTING, "human_approved")
        else:
            self._transition(HealingState.ESCALATED, "human_rejected")
        return self.session

    def record_guard(self, decision: Dict[str, Any], *, stop_on_deny: bool = True):
        assert self.session is not None
        self.session.guard_decision = decision
        if self.session_dir:
            (self.session_dir / "guard_decision.json").write_text(
                json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
        if decision["decision"] == "DENY" and stop_on_deny:
            self.session.recovery_success = False
            self.session.recovery_reason = decision["reason"]
            self._transition(HealingState.ESCALATED, "guard_rejected", "guard_decision.json")
            self.finalize(decision["reason"])
        self._save_session()

    def record_execution(self, result: Dict[str, Any]):
        assert self.session is not None
        self.session.execution_result = result
        self._save_session()

    def attach_attempt(self, attempt_id: str):
        assert self.session is not None
        if self.session.attempt_count >= self.session.max_attempts:
            raise ValueError("Recovery attempt budget exhausted")
        if self.machine.state not in {
            HealingState.REPAIR_PLANNED,
            HealingState.AWAITING_APPROVAL,
            HealingState.REPAIR_EXECUTING,
        }:
            raise ValueError("Repair attempt cannot be attached in current state")
        if self.machine.state == HealingState.AWAITING_APPROVAL:
            if self.session.approved_by_human is not True:
                raise ValueError("Human approval is required before attaching an attempt")
        elif self.machine.state == HealingState.REPAIR_PLANNED:
            self._transition(HealingState.REPAIR_EXECUTING, "repair_attempt_started")
        self.session.repair_attempt_ids.append(attempt_id)
        self.session.attempt_count = len(self.session.repair_attempt_ids)
        self._save_session()
        return self.session

    def attach_verification(self, verification: RecoveryVerificationResult):
        assert self.session is not None
        if self.machine.state != HealingState.REPAIR_EXECUTING:
            raise ValueError("Verification requires a repair executing state")
        self._transition(HealingState.VERIFYING, "verification_started")
        self.session.recovery_success = verification.recovered
        self.session.verification_result = verification.model_dump()
        self.session.completed_at = datetime.now(timezone.utc).isoformat()
        self.session.recovery_reason = verification.reason
        target = HealingState.RECOVERED if verification.recovered else HealingState.NOT_RECOVERED
        self._transition(target, "verification_completed", "verification/recovery_verification.json")
        if self.session_dir:
            verification_dir = self.session_dir / "verification"
            verification_dir.mkdir(exist_ok=True)
            (verification_dir / "recovery_verification.json").write_text(
                json.dumps(verification.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (self.session_dir / "final_result.json").write_text(json.dumps({
                "schema_version": "1.0", "recovery_session_id": self.session.recovery_session_id,
                "state": self.session.state.value, "recovery_success": verification.recovered,
                "reason": verification.reason,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.session

    def finalize(self, reason: str = ""):
        assert self.session is not None
        if self.machine.state != HealingState.TERMINATED:
            if self.machine.can_transition(HealingState.TERMINATED):
                self._transition(HealingState.TERMINATED, "session_terminated")
            else:
                self.session.recovery_reason = reason or self.session.recovery_reason
                self.session.completed_at = datetime.now(timezone.utc).isoformat()
                self._save_session()
        self.session.completed_at = self.session.completed_at or datetime.now(timezone.utc).isoformat()
        self._save_session()
        return self.session
