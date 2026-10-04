"""Parser for converting raw LLM text or dicts into typed Action models."""

import json
import re
from typing import Any, Dict, Union

from pydantic import ValidationError

from diagagent.actions.schema import ACTION_MODEL_MAP, BaseAction
from diagagent.core.errors import ActionParseError


class ActionParser:
    """Robust parser for LLM outputs and JSON/dict actions."""

    @staticmethod
    def extract_json(raw_text: str) -> Dict[str, Any]:
        """Extracts JSON object from text, handling markdown code fences."""
        text = raw_text.strip()
        # Look for ```json ... ``` or ``` ... ```
        pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            # Look for outermost { ... }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]

        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ActionParseError(f"Extracted JSON is not an object: {type(parsed)}")
            return parsed
        except json.JSONDecodeError as err:
            raise ActionParseError(f"Failed to decode JSON from text: {err}") from err

    @classmethod
    def parse(cls, raw: Union[str, Dict[str, Any]]) -> BaseAction:
        """Parses a string or dictionary into a typed Action model."""
        if isinstance(raw, BaseAction):
            if raw.type not in ACTION_MODEL_MAP:
                raise ActionParseError(f"Unsupported action: {raw.type}")
            return raw

        if isinstance(raw, str):
            data = cls.extract_json(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            raise ActionParseError(f"Expected dict or str, got {type(raw).__name__}")

        action_type = data.get("type")
        if not action_type:
            raise ActionParseError("Missing 'type' field in action specification")

        action_type_str = str(action_type).lower().strip()
        model_cls = ACTION_MODEL_MAP.get(action_type_str)

        if not model_cls:
            raise ActionParseError(f"Unsupported action: {action_type_str}")

        try:
            return model_cls(**data)
        except ValidationError as err:
            errors = err.errors()
            msg = "; ".join(f"{e.get('loc', '')}: {e.get('msg', '')}" for e in errors)
            raise ActionParseError(f"Validation failed for action '{action_type_str}': {msg}") from err
