"""HTTP server: static player UI, audio files, and a small JSON API over the library."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict
from urllib.parse import unquote, urlparse

from woodshed import analysis, fetch, library
from woodshed.resources import MODEL_PATH, WEB_DIRECTORY

DEFAULT_PORT = 8377
PARENT_POLL_SECONDS = 1.0
REQUIRED_PROGRAMS = ["/usr/bin/afconvert", "/usr/bin/osascript"]  # both ship with macOS

_analysis_lock = threading.Lock()
_songs_being_analyzed: set[str] = set()
_analysis_errors: dict[str, str] = {}


class SongResponse(TypedDict):
    id: str
    analysis: dict[str, object] | None
    analysis_error: str | None
    notes: dict[str, object]


def start_analysis_if_needed(song_id: str) -> None:
    with _analysis_lock:
        if song_id in _songs_being_analyzed or library.analysis_path(song_id).exists():
            return
        _songs_being_analyzed.add(song_id)
    threading.Thread(target=_analyze_song, args=(song_id,), daemon=True).start()


def _analyze_song(song_id: str) -> None:
    try:
        library.save_analysis(song_id, analysis.analyze_audio_file(library.audio_path(song_id)))
        _analysis_errors.pop(song_id, None)
    except Exception as error:  # surfaced to the UI; the player still works without a grid
        _analysis_errors[song_id] = str(error)
    finally:
        with _analysis_lock:
            _songs_being_analyzed.discard(song_id)


def parse_notes(raw: dict[str, object]) -> library.Notes:
    loop = raw.get("loop")
    if loop is not None and not isinstance(loop, dict):
        raise ValueError("loop must be an object or null")
    fields = {name: value for name, value in raw.items() if name != "loop"}
    return library.Notes(**fields, loop=library.LoopRegion(**loop) if loop else None)  # type: ignore[arg-type]  # validated by the dataclass constructors raising TypeError on unknown fields


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_DIRECTORY), **kwargs)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature fixed by the base class
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parts = self._path_parts()
        try:
            if parts == ["api", "songs"]:
                self._send_json(library.list_song_ids())
            elif len(parts) == 3 and parts[:2] == ["api", "songs"]:
                self._send_song(parts[2])
            elif len(parts) == 2 and parts[0] == "audio":
                self._send_audio(parts[1])
            else:
                super().do_GET()
        except (ValueError, FileNotFoundError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parts = self._path_parts()
        if len(parts) != 4 or parts[:2] != ["api", "songs"] or parts[3] != "notes":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            library.save_notes(parts[2], parse_notes(self._read_json()))
        except (ValueError, TypeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"saved": True})

    def do_POST(self) -> None:
        parts = self._path_parts()
        if parts == ["api", "log"]:
            # The player reports its own errors here, so they land in the same log file as the
            # server's: inside the app there is no browser console to look at.
            print(f"[player] {str(self._read_json().get('message'))[:2000]}", flush=True)
            self._send_json({"logged": True})
            return
        if parts != ["api", "grab"]:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        url = self._read_json().get("url")
        try:
            grabbed = fetch.grab_from_url(url) if isinstance(url, str) and url else fetch.grab_from_spotify()
        except fetch.FetchError as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        start_analysis_if_needed(grabbed.song_id)
        self._send_json(asdict(grabbed))

    def _path_parts(self) -> list[str]:
        return [unquote(part) for part in urlparse(self.path).path.split("/") if part]

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return body

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_song(self, song_id: str) -> None:
        if not library.audio_path(song_id).exists():
            raise FileNotFoundError(f"no such song: {song_id}")
        start_analysis_if_needed(song_id)
        song_analysis = library.load_analysis(song_id)
        response: SongResponse = {
            "id": song_id,
            "analysis": asdict(song_analysis) if song_analysis else None,
            "analysis_error": _analysis_errors.get(song_id),
            "notes": asdict(library.load_notes(song_id)),
        }
        self._send_json(response)

    def _send_audio(self, song_id: str) -> None:
        path = library.audio_path(song_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as audio_file:
            shutil.copyfileobj(audio_file, self.wfile)


def check_startup_requirements() -> None:
    """Fail loudly at launch rather than on the first grab."""
    missing = [program for program in REQUIRED_PROGRAMS if shutil.which(program) is None]
    missing += [str(path) for path in (MODEL_PATH, WEB_DIRECTORY / "index.html") if not path.exists()]
    if missing:
        raise SystemExit(f"woodshed: missing at startup: {', '.join(missing)}")


def exit_when_parent_dies() -> None:
    """The app shell starts this server as a child; if the shell crashes, don't linger as an orphan."""
    original_parent = os.getppid()
    while os.getppid() == original_parent:
        time.sleep(PARENT_POLL_SECONDS)
    os._exit(0)


def serve(port: int = DEFAULT_PORT, exit_with_parent: bool = False) -> None:
    check_startup_requirements()
    threading.Thread(target=analysis.warm_up, daemon=True).start()
    if exit_with_parent:
        threading.Thread(target=exit_when_parent_dies, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"woodshed listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
