"""Unit tests for Mock and GUI Executors."""

from diagagent.actions.schema import ClickAction, WaitAction
from diagagent.environment.mock_gimp import MockGIMPBackend
from diagagent.executor.mock_executor import MockExecutor


def test_mock_executor():
    backend = MockGIMPBackend()
    executor = MockExecutor(backend=backend)

    res = executor.execute(ClickAction(x=50, y=50))
    assert res.success is True
    assert res.state_changed is True

    res_wait = executor.execute(WaitAction(duration=0.1))
    assert res_wait.success is True
    assert res_wait.state_changed is False
