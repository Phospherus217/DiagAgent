"""Model protocol and message schemas for multimodal agent decision making."""

from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable
from pydantic import BaseModel, Field


class Message(BaseModel):
    """Represents a structured interaction message in the agent-model dialogue."""

    role: Literal["system", "user", "assistant"]
    content: str
    images: List[str] = Field(default_factory=list)  # File paths or base64 data URIs


class ModelResponse(BaseModel):
    """Normalized response from a foundation or mock model."""

    text: str
    raw: Optional[Dict[str, Any]] = None
    latency_ms: Optional[int] = None
    provider: str = "mock"
    model: str = "mock-model"


@runtime_checkable
class Model(Protocol):
    """Protocol for models generating actions from dialogue messages and GUI observations."""

    def query(
        self,
        messages: List[Message],
        observation: Optional[Any] = None,
    ) -> ModelResponse:
        """Queries the model for the next step."""
        ...
