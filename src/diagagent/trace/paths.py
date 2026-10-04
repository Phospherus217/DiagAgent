"""Resolve only existing evidence inside a run, including moved legacy runs."""
from pathlib import Path


def evidence_path(root, reference):
    root = Path(root).resolve()
    if not reference:
        return None
    path = Path(reference)
    if path.is_absolute():
        # Legacy traces used absolute paths. Rebase only known evidence directories.
        try:
            path = path.relative_to(root)
        except ValueError:
            parts = path.parts
            indices = [i for i, part in enumerate(parts) if part in {"screenshots", "artifacts"}]
            if not indices:
                return None
            path = Path(*parts[indices[-1]:])
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved
