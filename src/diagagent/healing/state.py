"""Explicit state machine for one recovery session."""

from typing import Dict, Iterable, Set

from diagagent.schemas.recovery import HealingState


class InvalidHealingTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: Dict[HealingState, Set[HealingState]] = {
    HealingState.RUNNING: {HealingState.FAILURE_DETECTED, HealingState.TERMINATED},
    HealingState.FAILURE_DETECTED: {HealingState.DIAGNOSING, HealingState.ESCALATED, HealingState.TERMINATED},
    HealingState.DIAGNOSING: {HealingState.DIAGNOSED, HealingState.ESCALATED},
    HealingState.DIAGNOSED: {HealingState.RECOVERY_DECISION, HealingState.ESCALATED},
    HealingState.RECOVERY_DECISION: {HealingState.REPAIR_PLANNED, HealingState.ESCALATED, HealingState.TERMINATED},
    HealingState.REPAIR_PLANNED: {HealingState.AWAITING_APPROVAL, HealingState.REPAIR_EXECUTING, HealingState.ESCALATED},
    HealingState.AWAITING_APPROVAL: {HealingState.REPAIR_EXECUTING, HealingState.ESCALATED, HealingState.TERMINATED},
    HealingState.REPAIR_EXECUTING: {HealingState.VERIFYING, HealingState.NOT_RECOVERED, HealingState.ESCALATED},
    HealingState.VERIFYING: {HealingState.RECOVERED, HealingState.NOT_RECOVERED, HealingState.ESCALATED},
    HealingState.RECOVERED: {HealingState.RUNNING, HealingState.TERMINATED},
    HealingState.NOT_RECOVERED: {HealingState.ESCALATED, HealingState.TERMINATED},
    HealingState.ESCALATED: {HealingState.TERMINATED},
    HealingState.TERMINATED: set(),
}


class HealingStateMachine:
    def __init__(self, state: HealingState = HealingState.FAILURE_DETECTED):
        self.state = state

    def can_transition(self, target: HealingState) -> bool:
        return target in ALLOWED_TRANSITIONS[self.state]

    def transition(self, target: HealingState) -> HealingState:
        target = HealingState(target)
        if not self.can_transition(target):
            raise InvalidHealingTransition(f"{self.state.value} -> {target.value} is not allowed")
        self.state = target
        return self.state

