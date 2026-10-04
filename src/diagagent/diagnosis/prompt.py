"""Stable prompts for evidence-grounded hybrid diagnosis."""

import json
from typing import Any, Dict


SYSTEM_PROMPT = """You are a diagnosis assistant. Use only the supplied public evidence.
Do not invent failures, hidden task labels, evaluator internals, credentials, or
causal claims unsupported by evidence. Return one JSON object with keys:
failure_type, first_failure_step, confidence, explanation, repair_suggestion.
Use null when evidence cannot localize a step. Confidence is a number from 0 to 1."""


def build_messages(evidence: Dict[str, Any]):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Public execution evidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)},
    ]


__all__ = ["SYSTEM_PROMPT", "build_messages"]

