"""Command line: `woodshed` grabs what Spotify is playing and opens it in the player.

  woodshed            grab the current Spotify track
  woodshed <url>      grab a YouTube (or any yt-dlp) URL
  woodshed --open     just open the player
  woodshed --serve    run the server in the foreground
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from woodshed.server import DEFAULT_PORT, serve

BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = Path.home() / "Library" / "Logs" / "woodshed.log"
SERVER_START_TIMEOUT_SECONDS = 30


def server_is_up() -> bool:
    try:
        urllib.request.urlopen(f"{BASE_URL}/api/songs", timeout=1)
    except (urllib.error.URLError, TimeoutError):
        return False
    return True


def ensure_server_running() -> None:
    if server_is_up():
        return
    with LOG_FILE.open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "woodshed", "--serve"], stdout=log, stderr=log, start_new_session=True, cwd=PROJECT_ROOT)
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if server_is_up():
            return
        time.sleep(0.2)
    raise SystemExit(f"woodshed: server didn't start; see {LOG_FILE}")


def notify(message: str) -> None:
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(message, ensure_ascii=False)} with title "woodshed"'])


def grab(url: str | None) -> None:
    notify("Fetching…")
    request = urllib.request.Request(
        f"{BASE_URL}/api/grab", data=json.dumps({"url": url}).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        grabbed = json.load(urllib.request.urlopen(request, timeout=180))
    except urllib.error.HTTPError as error:
        message = json.load(error).get("error", "grab failed")
        notify(message)
        raise SystemExit(f"woodshed: {message}") from error
    subprocess.run(["open", f"{BASE_URL}/#song={quote(grabbed['song_id'])}&t={grabbed['position_seconds']}"])


def main() -> None:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--serve"]:
        # `--serve [port]` on its own is the dev server; the app shell adds --exit-with-parent
        port = next((int(argument) for argument in arguments[1:] if argument.isdigit()), DEFAULT_PORT)
        serve(port, exit_with_parent="--exit-with-parent" in arguments)
        return
    ensure_server_running()
    if arguments == ["--open"]:
        subprocess.run(["open", BASE_URL])
    else:
        grab(arguments[0] if arguments else None)


if __name__ == "__main__":
    main()
