"""Environment package for DiagAgent."""

from diagagent.environment.base import BaseEnvironment
from diagagent.environment.gimp_window import GIMPWindowManager
from diagagent.environment.mock_gimp import MockGIMPBackend
from diagagent.environment.real_gimp import RealGIMPBackend
from diagagent.environment.gimp_env import GIMPEnvironment

__all__ = [
    "BaseEnvironment",
    "GIMPWindowManager",
    "MockGIMPBackend",
    "RealGIMPBackend",
    "GIMPEnvironment",
]
