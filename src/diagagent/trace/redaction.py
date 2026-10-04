"""Credential scrubbing at the persistence boundary, including nested text."""
import json
import re


class Redactor:
    def __init__(self):
        self.secrets = set()

    def register(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)

    def clean(self, value):
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        if isinstance(value, dict):
            return {str(k): self.clean(v) for k, v in value.items()
                    if not re.search(r"authorization|api.?key|password|access.?token|secret", str(k), re.I)}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if not isinstance(value, str):
            return value
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        # Model text may itself contain JSON credential fields.
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(self.clean(parsed), ensure_ascii=False)
        value = re.sub(r"(?i)(?:authorization\s*[:=]\s*|bearer\s+)[^\s,;]+(?:\s+[^\s,;]+)?", "[REDACTED]", value)
        value = re.sub(r"(?i)(?:api[_-]?key|password|access_token)\s*[:=]\s*[^\s&;,]+", "[REDACTED]", value)
        value = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[REDACTED]@", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
