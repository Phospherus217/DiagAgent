"""Atomic execution helpers for mouse and keyboard actions."""

import time
from typing import List, Tuple, Union


def wait_seconds(seconds: float) -> None:
    """Pauses execution for a specified duration."""
    time.sleep(max(0.0, float(seconds)))


def normalize_coordinates(x: float, y: float) -> Tuple[int, int]:
    """Rounds and converts coordinates to integer screen pixels."""
    return int(round(x)), int(round(y))
