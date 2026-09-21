# Woodshed

A Mac app for learning songs by ear. Press one hotkey while Spotify is playing — or paste a
YouTube link — and the song opens in a player that has already found its beats and bars. Then:

- **step back** a beat, a bar, or a transient — no seek handle;
- **loop a section** with the loop points snapped exactly to the grid;
- **slow it down** without changing pitch;
- **label the timeline in Nashville numbers** — `1`, `4`, `6m`, `b7` — as you work the chords out.

No projects, no Save button. Everything autosaves next to the audio. It is deliberately not a
DAW: hands stay on the instrument.

Personal tool, shared as-is. Apple Silicon Macs only. MIT.

## Get started

There is no downloadable installer yet (the build is only signed for the Mac that made it),
so you build the app yourself. It takes one command and a few minutes.

You need:

- an Apple Silicon Mac running macOS 13 or later;
- Xcode or the Xcode Command Line Tools (`xcode-select --install`);
- [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

Then:

```bash
git clone https://github.com/borispetrovdev/woodshed.git
cd woodshed
scripts/build_app.sh
```

The first build downloads the beat-tracking model's dependencies (PyTorch, ~1 GB, only ever
used to export the model) and takes several minutes; later builds take about 90 seconds. The
result is `build/Woodshed.app` — drag it into `/Applications` and open it.

## Using it

Two ways in:

- **From Spotify:** play a song, press **⌃⌥⌘W** (from any app). Woodshed pauses Spotify,
  fetches the song and opens it at the same spot. The first time, macOS asks whether Woodshed
  may control Spotify — say yes; that is how it learns which song is playing.
- **From a link:** press `o`, paste a YouTube URL. No Spotify needed.

Either way it finds the beats in the background (about 20 seconds; you can play and slow down
straight away), and opening the same song again is instant.

| keys | |
|---|---|
| `space` | play / pause |
| `←` `→` | back / forward one beat — hold `⇧` for a bar, `⌥` for a transient |
| `[` `]` | loop in / out at the nearest beat (same modifiers); `l` toggles the loop, `r` restarts it |
| `,` `.` / `<` `>` | slide the loop by its own length / halve or double it |
| `-` `=` `0` | speed down / up 5% / back to 100% |
| `1`–`7`, `b`, `#` or `↵` | label the current beat; `tab` saves and moves to the next bar; click a label to edit; `⌫` deletes |
| `⌘Z` `⇧⌘Z` | undo / redo labels and loops |
| `d`, `m` | the downbeat is here / cycle beats per bar — for when the detected bars are wrong |
| `↑` `↓` or pinch | zoom; two-finger swipe scrolls; `⇧↑` `⇧↓` volume |
| `g`, `o` | grab from Spotify / open the song list (also takes a YouTube URL) |

Mouse: click to seek, drag to loop — both snap with the same modifiers.

`woodshed://grab` and `woodshed://grab?url=<youtube url>` do the same as the hotkey, for
launchers like Raycast, Alfred or Keyboard Maestro.

Songs live in `~/Music/Woodshed/`: `X.m4a` is the audio, `X.analysis.json` the detected grid
(delete it to re-analyze), `X.notes.json` your labels, loop, speed and position. Logs go to
`~/Library/Logs/Woodshed.log`.

## Hacking on it

The app is a Swift `WKWebView` window (`shell/main.swift`) around a vanilla-JS player
(`web/`) served by a small Python server (`woodshed/`), frozen with PyInstaller. Beat tracking
is [beat_this](https://github.com/CPJKU/beat_this) exported to ONNX so the app needs no
PyTorch; audio decoding uses macOS's own `afconvert` so it needs no ffmpeg. `CLAUDE.md` has
the design decisions and the reasons behind them.

```bash
uv run python -m woodshed --serve      # dev server at http://127.0.0.1:8377; reload picks up web/ edits
uv run pytest                          # guard tests
uv run python scripts/check_analysis_parity.py ~/Music/Woodshed/*.m4a   # ONNX pipeline vs PyTorch reference
```

To distribute a build to other Macs you need an Apple Developer ID: build with
`SIGN_IDENTITY="Developer ID Application: …" scripts/build_app.sh`, then `scripts/notarize.sh`
(its header has the one-time setup).

## Third-party pieces

- [Signalsmith Stretch](https://signalsmith-audio.co.uk/code/stretch/) (MIT) — time-stretching and looping, vendored in `web/vendor/`.
- [beat_this](https://github.com/CPJKU/beat_this) — the beat/downbeat model; the ONNX export is built locally from its published checkpoint and is not committed here. Check the checkpoint's licence before redistributing a build.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ONNX Runtime](https://onnxruntime.ai) — bundled into the app by PyInstaller.

Downloading audio from YouTube may be against its terms of service; use this on music you
have the right to use.
