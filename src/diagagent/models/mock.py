"""Deterministic mock model for reproducible testing and fault simulation."""

import json
import time
from typing import Any, Dict, List, Optional, Union
from diagagent.models.base import Message, ModelResponse


class MockModel:
    """Mock model returning pre-configured action scripts or dynamic rule responses."""

    def __init__(
        self,
        responses: Optional[List[Union[str, Dict[str, Any]]]] = None,
        model_name: str = "mock-v1",
        default_action: str = '{"type": "stop"}',
    ):
        self.responses = list(responses) if responses is not None else []
        self.model_name = model_name
        self.default_action = default_action
        self.query_count = 0
        self.history: List[List[Message]] = []

    def query(
        self,
        messages: List[Message],
        observation: Optional[Any] = None,
    ) -> ModelResponse:
        t0 = time.monotonic()
        self.query_count += 1
        self.history.append(messages)

        if self.responses:
            next_resp = self.responses.pop(0)
            if isinstance(next_resp, dict):
                text = json.dumps(next_resp)
            else:
                text = str(next_resp)
        else:
            text = self.default_action

        latency_ms = int((time.monotonic() - t0) * 1000)
        return ModelResponse(
            text=text,
            raw={"query_index": self.query_count},
            latency_ms=latency_ms,
            provider="mock",
            model=self.model_name,
        )
