"""App-wide preferences (as opposed to per-song notes), kept out of the song library."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_PATH = Path.home() / "Library" / "Application Support" / "Woodshed" / "settings.json"


@dataclass
class Settings:
    volume: float = 1.0  # slider position, 0..1

    def __post_init__(self) -> None:
        if isinstance(self.volume, bool) or not isinstance(self.volume, (int, float)) or not 0.0 <= self.volume <= 1.0:
            raise ValueError(f"volume must be a number between 0 and 1, got {self.volume!r}")


def load_settings() -> Settings:
    if not SETTINGS_PATH.exists():
        return Settings()
    try:
        return Settings(**json.loads(SETTINGS_PATH.read_text()))
    except (ValueError, TypeError):
        return Settings()  # a corrupt or outdated preferences file must never stop the app from opening


def save_settings(settings: Settings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(asdict(settings)))
