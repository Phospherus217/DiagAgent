"""Explicit repair executor facade.

Keeping this small facade lets callers depend on the v0.2 module layout while
the existing runner remains the single implementation of execution policy.
"""

from pathlib import Path
from typing import Any, Optional, Tuple, Union


def execute_repair(plan_file: Union[str, Path], output_dir: Union[str, Path], config: Optional[Any] = None) -> Tuple[Path, Any]:
    from diagagent.repair.runner import execute_plan

    return execute_plan(plan_file, output_dir, config=config)


__all__ = ["execute_repair"]
