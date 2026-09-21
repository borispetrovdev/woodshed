# Woodshed

Local practice player: grab whatever Spotify is playing, loop it on the beat grid, slow it
down, and label it in numbers. No projects, no saving — everything autosaves next to the audio
in `~/Music/Woodshed/`.

    woodshed            # grab the current Spotify track and open it (bind this to a hotkey)
    woodshed <url>      # grab a YouTube URL instead
    woodshed --open     # just open the player

The server (http://127.0.0.1:8377) starts itself on first use; logs go to `~/Library/Logs/woodshed.log`.

| keys | |
|---|---|
| `space` | play / pause |
| `←` `→` | back / forward one beat — hold `⇧` for a bar, `⌥` for a transient |
| `[` `]` | loop in / out at the nearest beat (same modifiers); `l` toggles, `r` restarts |
| `,` `.` / `<` `>` | slide the loop by its own length / halve or double it |
| `-` `=` `0` | speed down / up 5% / back to 100% (pitch is preserved) |
| `1`–`7`, `b`, `#` or `↵` | label the current beat; in the editor `tab` = next bar, `⌥→` = next beat |
| `d`, `m` | "the downbeat is here" / cycle beats per bar, when the detected bars are wrong |
| `↑` `↓` | zoom · `g` grab from Spotify · `o` song list |

Mouse: click to seek, drag to loop — both snap with the same modifiers.

Per song, `X.m4a` is the audio, `X.analysis.json` is machine-made (delete to re-analyze) and
`X.notes.json` is yours (labels, loop, speed, key).

## The Mac app

    scripts/build_app.sh        # → build/Woodshed.app and build/Woodshed-<version>.dmg

Woodshed.app is a small Swift window (`shell/main.swift`) around the same player UI, with the
Python server frozen by PyInstaller inside it. It needs nothing installed: no Python, Homebrew,
ffmpeg or yt-dlp (decoding and remuxing use macOS's own `afconvert`; the beat tracker runs on
ONNX Runtime). In the app, `⌃⌥⌘W` from anywhere grabs the current Spotify track, as does opening
`woodshed://grab` (or `woodshed://grab?url=<youtube url>`). Logs: `~/Library/Logs/Woodshed.log`.

A plain build is ad-hoc signed and only runs on the Mac that built it. To distribute, build with
a Developer ID and notarize — `scripts/notarize.sh` has the one-time setup in its header.

The ONNX beat model is exported from the beat_this checkpoint by `scripts/export_beat_model.py`
(automatic on first build); `scripts/check_analysis_parity.py` checks the NumPy/ONNX pipeline
against the PyTorch reference. Both need the dev dependency group.

Tests: `uv run pytest`.

## Third-party pieces

- [Signalsmith Stretch](https://signalsmith-audio.co.uk/code/stretch/) (MIT) — time-stretching and looping, vendored in `web/vendor/`.
- [beat_this](https://github.com/CPJKU/beat_this) — the beat/downbeat model; the ONNX export is built locally from its published checkpoint and is not committed here. Check the checkpoint's licence before redistributing a build.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ONNX Runtime](https://onnxruntime.ai) — bundled into the app by PyInstaller.

Downloading audio from YouTube may be against its terms of service; this is a personal practice tool — use it on music you have the right to use.

## Status

Works on the Mac it was built on. The DMG is ad-hoc signed, so there is no download for other
Macs yet — build from source with `scripts/build_app.sh` (Apple Silicon, Xcode command line
tools and [uv](https://docs.astral.sh/uv/)). MIT licensed.
