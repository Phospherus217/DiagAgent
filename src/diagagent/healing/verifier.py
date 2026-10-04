"""Independent process/artifact/task verification for recovery attempts."""

from typing import Any, Dict, Optional

from diagagent.schemas.recovery import RecoverySession, RecoveryVerificationResult, VerificationContract


class RecoveryVerifier:
    def verify(self, *, session: RecoverySession, attempt: Any,
               contract: VerificationContract) -> RecoveryVerificationResult:
        data = attempt.model_dump() if hasattr(attempt, "model_dump") else dict(attempt or {})
        process = self._check(data, "process_recovered", "process_passed")
        artifact = self._check(data, "artifact_recovered", "artifact_passed")
        task = self._check(data, "task_recovered", "task_success")
        side_effects = data.get("violated_side_effects") or data.get("side_effects") or []
        if not isinstance(side_effects, list):
            side_effects = [str(side_effects)]
        refs = data.get("evidence_refs") or []
        if not isinstance(refs, list):
            refs = [str(refs)]
        required = {str(item).lower() for item in (contract.required_checks or
                                                    ["process", "artifact", "task"])}
        unmet = []
        aliases = {
            "process": process, "process_step_pass": process,
            "artifact": artifact, "artifact_exists": artifact,
            "artifact_pass": artifact, "task": task, "task_success": task,
        }
        measured_checks = data.get("checks") or {}
        # An unimplemented required check is UNKNOWN, never implicitly PASS.
        for name in required:
            value = aliases.get(name, measured_checks.get(name))
            if value is not True:
                unmet.append(name)
        # The result schema requires all three independent outcomes even when
        # a caller supplies a narrower contract. Fail closed without raising.
        for name, value in (("process", process), ("artifact", artifact), ("task", task)):
            if value is not True:
                unmet.append(name)
        side_checks = data.get("side_effect_checks") or {}
        for name in contract.forbidden_side_effects:
            if side_checks.get(name) is not True:
                unmet.append(f"side_effect:{name}")
                if side_checks.get(name) is False and name not in side_effects:
                    side_effects.append(name)
        for scope, expected, actual in (
            ("state", contract.expected_state_change, data.get("state_after") or {}),
            ("artifact", contract.expected_artifact_change, data.get("artifact_after") or {}),
        ):
            for name, value in expected.items():
                if name not in actual or actual[name] != value:
                    unmet.append(f"{scope}:{name}")
        if contract.task_success_required and task is not True and "task" not in required and "task_success" not in required:
            unmet.append("task_success")
        recovered = not unmet and not side_effects
        known = sum(value is not None for value in (process, artifact, task))
        confidence = 1.0 if recovered else 0.5 if known == 3 else 0.0
        reason = "Recovery checks passed independently." if recovered else (
            "Recovery is not established: one or more process/artifact/task checks failed or are unknown."
        )
        return RecoveryVerificationResult(
            process_recovered=process, artifact_recovered=artifact, task_recovered=task,
            violated_side_effects=[str(item) for item in side_effects], recovered=recovered,
            confidence=confidence, evidence_refs=[str(item) for item in refs], reason=reason,
            unmet_checks=sorted(set(unmet)),
        )

    @staticmethod
    def _check(data: Dict[str, Any], primary: str, alias: str) -> Optional[bool]:
        value = data.get(primary, data.get(alias))
        if value is None:
            return None
        return value if isinstance(value, bool) else None
