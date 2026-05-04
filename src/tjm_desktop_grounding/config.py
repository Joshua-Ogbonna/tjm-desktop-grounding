from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    target_label: str = "Notepad"
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0
    launch_timeout_seconds: float = 8.0
    type_interval_seconds: float = 0.001
    save_directory: Path = Path.home() / "Desktop" / "tjm-project"


def default_config() -> AppConfig:
    return AppConfig()
