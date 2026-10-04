"""Explicit, local desktop configuration; no credentials or model calls."""
from pathlib import Path
from typing import Optional, Tuple
import yaml
from pydantic import BaseModel, ConfigDict, Field


class DesktopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gimp_executable: Optional[str] = None
    profile_id: str = "gimp32-en-dpi96-v1"
    language: str = "en_US"
    window_size: Tuple[int, int] = (1440, 900)
    expected_dpi: int = 96
    expected_screen_size: Optional[Tuple[int, int]] = None
    startup_timeout: float = Field(default=45, gt=0, le=120)
    gui_timeout: float = Field(default=10, gt=0, le=60)
    focus_timeout: float = Field(default=5, gt=0, le=30)
    run_timeout: float = Field(default=180, gt=0, le=600)
    max_primitives: int = Field(default=100, gt=0, le=500)
    max_format_repairs: int = Field(default=1, ge=0, le=1)

    @classmethod
    def load(cls, path=None):
        if path is None:
            return cls()
        source = Path(path).resolve()
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if data.get("gimp_executable"):
            executable = Path(data["gimp_executable"])
            data["gimp_executable"] = str(executable if executable.is_absolute() else source.parent / executable)
        return cls(**data)
