"""Mock executor for simulation and automated testing."""

import time
from typing import Any, Optional

from diagagent.actions.schema import BaseAction
from diagagent.executor.base import BaseExecutor
from diagagent.executor.models import ExecutionResult


class MockExecutor(BaseExecutor):
    """Executes actions against a virtual/mock environment state without actual OS mouse/keyboard events."""

    def __init__(self, backend: Optional[Any] = None):
        self.backend = backend
        self.execution_history = []

    def execute(self, action: BaseAction) -> ExecutionResult:
        start_time = time.time()
        self.execution_history.append(action)

        action_type = action.type
        state_changed = action_type not in {"wait", "stop"}

        if self.backend is not None and hasattr(self.backend, "execute_atomic"):
            try:
                res = self.backend.execute_atomic(action.to_dict())
                duration = (time.time() - start_time) * 1000.0
                return ExecutionResult(
                    success=True,
                    state_changed=res.get("changed", state_changed),
                    action_executable=True,
                    duration_ms=duration,
                    metadata={"backend_message": res.get("message", "mock executed")},
                )
            except Exception as err:
                duration = (time.time() - start_time) * 1000.0
                return ExecutionResult(
                    success=False,
                    state_changed=False,
                    action_executable=False,
                    error_type="Execution No-op",
                    error_message=str(err),
                    duration_ms=duration,
                )

        duration = (time.time() - start_time) * 1000.0
        return ExecutionResult(
            success=True,
            state_changed=state_changed,
            action_executable=True,
            duration_ms=duration,
            metadata={"type": action_type},
        )
