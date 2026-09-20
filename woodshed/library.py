"""On-disk song library: one audio file per song, plus analysis and notes sidecars.

Analysis (machine-made, regenerable) and notes (hand-made, precious) live in separate
files so re-running analysis can never clobber labels.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

LIBRARY_DIRECTORY = Path.home() / "Music" / "Woodshed"
AUDIO_EXTENSION = ".m4a"


@dataclass
class Analysis:
    duration_seconds: float
    beat_times: list[float]
    downbeat_times: list[float]
    transient_times: list[float]


@dataclass
class LoopRegion:
    start_seconds: float
    end_seconds: float
    enabled: bool


@dataclass
class Notes:
    """Everything the player saves on the user's behalf. Labels are keyed by beat time so
    they survive a change of meter or downbeat."""
    key: str = ""
    speed: float = 1.0
    position_seconds: float = 0.0
    loop: LoopRegion | None = None
    labels: dict[str, str] = field(default_factory=dict)
    beats_per_bar_override: int | None = None
    downbeat_anchor_beat_index: int | None = None


def song_id_from_title(title: str) -> str:
    return title.replace("/", "_").replace(":", "_")


def audio_path(song_id: str) -> Path:
    return _checked(LIBRARY_DIRECTORY / f"{song_id}{AUDIO_EXTENSION}")


def analysis_path(song_id: str) -> Path:
    return _checked(LIBRARY_DIRECTORY / f"{song_id}.analysis.json")


def notes_path(song_id: str) -> Path:
    return _checked(LIBRARY_DIRECTORY / f"{song_id}.notes.json")


def _checked(path: Path) -> Path:
    """Song ids arrive from URLs; refuse any that would escape the library."""
    if path.resolve().parent != LIBRARY_DIRECTORY.resolve():
        raise ValueError(f"song id escapes the library: {path}")
    return path


def list_song_ids() -> list[str]:
    """Most recently used first. The player saves the playhead position into a song's notes as
    it plays, so the newest file of any kind marks the song last worked on."""
    LIBRARY_DIRECTORY.mkdir(parents=True, exist_ok=True)

    def last_used(song_id: str) -> float:
        paths = (audio_path(song_id), analysis_path(song_id), notes_path(song_id))
        return max(path.stat().st_mtime for path in paths if path.exists())

    song_ids = [file.stem for file in LIBRARY_DIRECTORY.glob(f"*{AUDIO_EXTENSION}")]
    return sorted(song_ids, key=last_used, reverse=True)


def load_analysis(song_id: str) -> Analysis | None:
    path = analysis_path(song_id)
    if not path.exists():
        return None
    return Analysis(**json.loads(path.read_text()))


def save_analysis(song_id: str, analysis: Analysis) -> None:
    analysis_path(song_id).write_text(json.dumps(asdict(analysis)))


def load_notes(song_id: str) -> Notes:
    path = notes_path(song_id)
    if not path.exists():
        return Notes()
    raw = json.loads(path.read_text())
    loop = raw.pop("loop", None)
    return Notes(**raw, loop=LoopRegion(**loop) if loop else None)


def save_notes(song_id: str, notes: Notes) -> None:
    temporary = notes_path(song_id).with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(notes), indent=1))
    temporary.replace(notes_path(song_id))
