"""Getting audio into the library: from whatever Spotify is playing, or from a URL."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
import yt_dlp.utils

from woodshed import library

DURATION_TOLERANCE_SECONDS = 3  # keeps us on the album cut, not a live version or video edit

# yt-dlp solves some of YouTube's challenges with a JavaScript runtime. It currently works
# without one (with fewer formats on offer), so a runtime is used when the machine has it and
# never required. Homebrew's and deno's own installer's locations are checked because the app
# does not inherit a shell PATH.
_DENO_LOCATIONS = ["/opt/homebrew/bin/deno", "/usr/local/bin/deno", str(Path.home() / ".deno" / "bin" / "deno")]


def _javascript_runtimes() -> dict[str, dict[str, str]]:
    deno = shutil.which("deno") or next((path for path in _DENO_LOCATIONS if Path(path).exists()), None)
    return {"deno": {"path": deno}} if deno else {}


_BASE_OPTIONS: dict[str, object] = {
    "format": "bestaudio[ext=m4a]",  # AAC: the one format macOS can remux and decode with no ffmpeg
    "noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
    "fixup": "never",  # the fixup step needs ffmpeg; afconvert does that job below
    "js_runtimes": _javascript_runtimes(),
}

_NOW_PLAYING_SCRIPT = """
tell application "Spotify"
  if player state is stopped then return ""
  set t to current track
  return (artist of t) & tab & (name of t) & tab & (duration of t) & tab & (player position as integer)
end tell
"""


class FetchError(Exception):
    pass


@dataclass
class SpotifyTrack:
    artist: str
    title: str
    duration_seconds: int
    position_seconds: int


@dataclass
class GrabbedSong:
    song_id: str
    position_seconds: int


def read_spotify_now_playing() -> SpotifyTrack:
    if subprocess.run(["/usr/bin/pgrep", "-xq", "Spotify"]).returncode != 0:
        raise FetchError("Spotify isn't running")
    result = subprocess.run(["/usr/bin/osascript", "-e", _NOW_PLAYING_SCRIPT], capture_output=True, text=True)
    if result.returncode != 0:
        raise FetchError(f"Couldn't ask Spotify what's playing: {result.stderr.strip()}")
    fields = result.stdout.rstrip("\n").split("\t")
    if len(fields) != 4:
        raise FetchError("Nothing playing in Spotify")
    artist, title, duration_milliseconds, position_seconds = fields
    return SpotifyTrack(artist, title, int(duration_milliseconds) // 1000, int(position_seconds))


def grab_from_spotify() -> GrabbedSong:
    track = read_spotify_now_playing()
    song_id = library.song_id_from_title(f"{track.artist} - {track.title}")
    if not library.audio_path(song_id).exists():
        query = f"{track.artist} {track.title}"
        low, high = track.duration_seconds - DURATION_TOLERANCE_SECONDS, track.duration_seconds + DURATION_TOLERANCE_SECONDS
        # First choice: a top-10 hit whose length matches Spotify's (usually the "Topic" upload).
        found = _download(song_id, f"ytsearch10:{query}", duration_filter=f"duration>={low} & duration<={high}")
        if not found and not _download(song_id, f"ytsearch1:{query} audio"):
            raise FetchError(f'Couldn\'t find "{track.title}" on YouTube')
    subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Spotify" to pause'])
    return GrabbedSong(song_id, track.position_seconds)


def grab_from_url(url: str) -> GrabbedSong:
    try:
        with yt_dlp.YoutubeDL({**_BASE_OPTIONS}) as downloader:
            information = downloader.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as error:
        raise FetchError(f"Couldn't read {url}: {error}") from error
    title = (information or {}).get("title")
    if not isinstance(title, str) or not title:
        raise FetchError(f"Couldn't read a title from {url}")
    song_id = library.song_id_from_title(title)
    if not library.audio_path(song_id).exists() and not _download(song_id, url):
        raise FetchError(f"Couldn't download {url}")
    return GrabbedSong(song_id, 0)


def _download(song_id: str, source: str, duration_filter: str | None = None) -> bool:
    """Fetch the best AAC stream and remux it into the library. Returns whether a song arrived."""
    library.LIBRARY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        options: dict[str, object] = {**_BASE_OPTIONS, "outtmpl": str(Path(directory) / "download.%(ext)s"), "max_downloads": 1}
        if duration_filter:
            options["match_filter"] = yt_dlp.utils.match_filter_func(duration_filter)
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([source])
        except yt_dlp.utils.MaxDownloadsReached:
            pass  # how yt-dlp reports "got the one I asked for"
        except yt_dlp.utils.DownloadError:
            return False
        downloaded = Path(directory) / "download.m4a"
        if not downloaded.exists():
            return False
        # YouTube serves fragmented MP4, which not every decoder opens; afconvert rewrites the
        # container without re-encoding the audio (-d 0), using nothing but macOS itself.
        remux = subprocess.run(
            ["/usr/bin/afconvert", "-f", "m4af", "-d", "0", str(downloaded), str(library.audio_path(song_id))],
            capture_output=True, text=True,
        )
        if remux.returncode != 0:
            raise FetchError(f"Couldn't prepare the downloaded audio: {remux.stderr.strip()}")
    return True
