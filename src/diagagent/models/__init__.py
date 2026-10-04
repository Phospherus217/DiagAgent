"""Models module providing model protocols and implementations."""

from diagagent.models.base import Message, Model, ModelResponse
from diagagent.models.mock import MockModel
from diagagent.models.openai_compatible import OpenAICompatibleModel

__all__ = [
    "Message",
    "Model",
    "ModelResponse",
    "MockModel",
    "OpenAICompatibleModel",
]
