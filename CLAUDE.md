# Woodshed

A Mac practice player for learning songs by ear, built by Boris Petrov (with Claude) on
2026-09-20. README.md covers usage, keys and build commands; this file is the context a
future agent can't get from the code: why it exists, why it's shaped this way, and what
state it's in.

## Why it exists

Boris learns music entirely by ear, playing along with recordings on a standalone piano
keyboard or guitar. While listening on Spotify he kept wanting three things on the spot:

1. go back a few "ticks" — never by grabbing a seek handle;
2. loop a section with the loop points exactly on a bar line or transient, so it loops cleanly;
3. slow the recording down.

He could do all of it in Logic Pro (download the song, drop it in a project, slice at
transients or beat-map, flex/varispeed) but the friction killed the impulse: a file to
download, a pile of Logic projects, and — his words — getting "distracted by various logic
parameters and software instruments, rather than just playing along". YouTube can step and
slow down but not loop. Spotify can do none of it (DRM: no speed control, no audio access).

He tried Capo first (same day). It loops and slows well, but chord annotations are absolute
names only, and he thinks in **Nashville numbers**. That was the tipping point for building
this: number labels typed straight onto the timeline, in the notation he actually uses.

**Product principles that follow** — hold new features to these:
- One keypress from "hearing a song" to "looping it slowed down". No import step.
- No projects, no Save. Everything autosaves beside the audio.
- Keyboard-first: his hands are on an instrument. Every core action has a key.
- Nothing to tinker with. It is deliberately not a DAW; resist knobs.
- Labels are his own by-ear analysis, as free text. No automatic chord detection — he
  derives by ear on purpose.

## Shape

- `woodshed/` — Python server (stdlib `http.server`, no framework): `library.py` (songs on
  disk: `X.m4a`, machine-made `X.analysis.json`, hand-made `X.notes.json` — kept separate so
  re-analysis can never clobber labels), `fetch.py` (Spotify now-playing via AppleScript;
  audio via in-process yt-dlp, choosing the YouTube hit whose duration matches Spotify's),
  `analysis.py`, `settings.py` (app-wide prefs), `server.py`, `resources.py` (dev vs frozen paths).
- `web/` — the whole UI: one vanilla-JS module, canvas-drawn, no build step. Audio is
  Signalsmith Stretch (WASM AudioWorklet, vendored), which has native `loopStart/loopEnd`.
- `shell/main.swift` — the Mac app: a WKWebView window that launches the frozen server as a
  child on a private port; `woodshed://grab` URL scheme; global hotkey `⌃⌥⌘W`.
- `scripts/`, `packaging/` — PyInstaller freeze, icon, signing, DMG, notarization.

## Decisions worth knowing before changing things

- **No PyTorch at runtime.** The beat tracker (beat_this, "final0") is exported to ONNX and
  the mel spectrogram / chunking / peak-picking are reimplemented in NumPy. This took the
  bundle from ~900 MB of dependencies to ~190 MB. `scripts/check_analysis_parity.py` proves
  the pipeline matches the PyTorch reference (100% of beats on the test songs); rerun it
  after touching `analysis.py`. Cost: ~2.6 s per 30 s chunk on CPU (~20 s a song, in the
  background). CoreML was tried and was slower; more than 4 threads was slower.
- **No ffmpeg, no Homebrew.** macOS's `/usr/bin/afconvert` decodes for analysis and remuxes
  YouTube's fragmented MP4 without re-encoding. This also avoids shipping a GPL ffmpeg.
  Consequence: downloads are AAC/m4a only.
- **The playhead is fixed and the waveform scrolls under it.** Sidescroll detaches the view
  (`viewOffset`) without touching playback; anything that moves the playhead glides back.
- **Playhead time is latency-corrected**, and the two tricky moments (just after a loop
  wrap, just after a transport restart) are handled in `currentPosition()` /
  `restartTransportAt()`. Every transport change must go through `restartTransportAt()`.
  A bug here looks like the view "flipping" for ~120 ms.
- **The loop stays armed when the playhead is beyond its end**; the engine loop is only
  applied when the playhead is before the loop end (the engine would otherwise wrap to an
  arbitrary phase).
- **All note edits go through `saveNotesSoon()`**, which is also where undo history is
  recorded. Speed, key and playhead position are excluded from undo. Playhead position is
  saved by one 1 Hz timer, by design — Boris asked for a single mechanism, not per-event hooks.
- **The Edit menu has no Undo item on purpose**: a menu key equivalent would swallow ⌘Z
  before the player's own undo sees it.
- **Ad-hoc builds skip the hardened runtime** (library validation rejects ad-hoc signed
  dylibs); Developer ID builds enable it. That path has never been exercised.

## Status (2026-09-21)

- Works on Boris's Mac; he has used it briefly and confirmed it works. Not yet through real
  practice sessions.
- **Nobody else can install it yet.** The DMG is ad-hoc signed, so it only runs on the Mac
  that built it. Distribution needs a Developer ID Application certificate (he has only an
  Apple Development one) plus notarization — see `scripts/notarize.sh`. There is no GitHub
  release. Until then, other people can only build from source (`scripts/build_app.sh`,
  needs Xcode command line tools and `uv`).
- Apple Silicon only. MIT licensed (LICENSE added 2026-09-21).
- Open questions before public distribution: the redistribution terms of the beat_this
  checkpoint; YouTube's terms of service; bundled yt-dlp goes stale as YouTube changes (it
  uses `deno` if present, and currently still works without).

## Working on it

- Dev loop: `uv run python -m woodshed --serve`, open http://127.0.0.1:8377. The server
  sends `no-store`, so a page reload picks up `web/` edits; Python edits need a restart.
  The app bundles its own copy of `web/` — rebuild (`scripts/build_app.sh`, ~90 s) and
  relaunch to see changes there.
- `window.woodshed` exposes `state`, `currentPosition()` and `engine()` for scripted
  checks. Synthetic key events need real `key`/`code` values (`BracketLeft`, etc.).
- An agent can't hear audio. Verify transport by sampling `currentPosition()` over time;
  ask Boris about anything that has to be judged by ear (stretch quality, playhead sync).
- Tests (`uv run pytest`) cover the guards on untrusted input; keep adding a test that
  proves a new guard fires. Python style: explicit names, typed boundaries, fail loudly at
  startup.
- When testing against the real library in `~/Music/Woodshed/`, clean up: notes files are
  Boris's own work.
