"""OpenAI-compatible Vision-Language Model adapter supporting standard multimodal endpoints."""

import base64
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error
import json

from diagagent.core.errors import ModelAPIError
from diagagent.models.base import Message, ModelResponse


class _ModelRequestError(ModelAPIError):
    """Safe request diagnostics within the existing ModelAPIError taxonomy."""

    def __init__(self, message, *, error, elapsed_time, timeout_seconds, status_code=None):
        super().__init__(message, status_code=status_code)
        # Preserve type names, never raw exception text, headers, URLs, or bodies.
        self.exception_type = type(error).__name__
        reason = getattr(error, "reason", None)
        self.reason_exception_type = type(reason).__name__ if isinstance(reason, BaseException) else None
        self.elapsed_time = elapsed_time  # Monotonic elapsed seconds.
        self.timeout_seconds = timeout_seconds

    def to_dict(self):
        return {
            **super().to_dict(),
            "exception_type": self.exception_type,
            "reason_exception_type": self.reason_exception_type,
            "elapsed_time": self.elapsed_time,
            "timeout_seconds": self.timeout_seconds,
        }


class OpenAICompatibleModel:
    """Multimodal adapter connecting to OpenAI, Qwen-VL, or vLLM compatible APIs."""

    def __init__(
        self,
        model_name: str = "gpt-4o",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self.model_name = model_name
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    def request_metadata(self) -> Dict[str, Any]:
        """Allowlisted audit data; never include headers or a credential-bearing URL."""
        model_name = self.model_name.replace(self.api_key, "[REDACTED]") if self.api_key else self.model_name
        return {
            "provider": "openai_compatible",
            "model": model_name,
            "method": "POST",
            "endpoint": "/chat/completions",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout,
        }

    def _encode_image(self, image_path: str) -> str:
        """Encodes an image file to base64 data URI."""
        p = Path(image_path)
        if not p.exists():
            return ""
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        with open(p, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def query(
        self,
        messages: List[Message],
        observation: Optional[Any] = None,
    ) -> ModelResponse:
        started = time.monotonic()

        def failure(message, error, status_code=None):
            return _ModelRequestError(
                message, error=error, status_code=status_code,
                elapsed_time=time.monotonic() - started, timeout_seconds=self.timeout,
            )

        try:
            return self._query(messages, observation)
        except ModelAPIError:
            raise
        except urllib.error.HTTPError as error:
            # HTTP reason/body/URL may echo secrets. Persist only the status code.
            message = ("Model API authentication failed (HTTP 401)" if error.code == 401
                       else f"Model API request failed (HTTP {error.code})")
            raise failure(message, error, status_code=error.code) from None
        except TimeoutError as error:
            raise failure("Model API request timed out", error) from None
        except urllib.error.URLError as error:
            message = ("Model API request timed out" if isinstance(error.reason, TimeoutError)
                       else "Model API network request failed")
            raise failure(message, error) from None
        except OSError as error:
            raise failure("Model API transport or input I/O failed", error) from None
        except Exception as error:
            raise failure("Model API request or response processing failed", error) from None

    def _query(
        self,
        messages: List[Message],
        observation: Optional[Any] = None,
    ) -> ModelResponse:
        t0 = time.monotonic()
        payload_messages: List[Dict[str, Any]] = []

        for msg in messages:
            if not msg.images:
                payload_messages.append({"role": msg.role, "content": msg.content})
            else:
                parts: List[Dict[str, Any]] = [{"type": "text", "text": msg.content}]
                for img in msg.images:
                    data_uri = self._encode_image(img) if not img.startswith("data:") else img
                    if data_uri:
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": data_uri}
                        })
                payload_messages.append({"role": msg.role, "content": parts})

        # Append screenshot from current observation if not already explicitly attached
        if observation and hasattr(observation, "screenshot_path") and observation.screenshot_path:
            obs_img = str(observation.screenshot_path)
            # Add to the last user message if exists
            if payload_messages and payload_messages[-1]["role"] == "user":
                last_content = payload_messages[-1]["content"]
                data_uri = self._encode_image(obs_img)
                if data_uri:
                    img_part = {"type": "image_url", "image_url": {"url": data_uri}}
                    if isinstance(last_content, str):
                        payload_messages[-1]["content"] = [
                            {"type": "text", "text": last_content},
                            img_part,
                        ]
                    elif isinstance(last_content, list) and img_part not in last_content:
                        last_content.append(img_part)

        request_body = {
            "model": self.model_name,
            "messages": payload_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
        latency_ms = int((time.monotonic() - t0) * 1000)
        choice = resp_data.get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "")
        return ModelResponse(
            text=text,
            raw=resp_data,
            latency_ms=latency_ms,
            provider="openai_compatible",
            model=self.model_name,
        )
