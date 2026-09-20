"""Where bundled, read-only files live: the project checkout in development, the PyInstaller
bundle directory when frozen inside Woodshed.app."""
from __future__ import annotations

import sys
from pathlib import Path

_FROZEN_BASE = getattr(sys, "_MEIPASS", None)
_BASE = Path(_FROZEN_BASE) if _FROZEN_BASE else Path(__file__).resolve().parent.parent

WEB_DIRECTORY = _BASE / "web"
MODEL_PATH = _BASE / "woodshed" / "models" / "beat_this_final0.onnx"
