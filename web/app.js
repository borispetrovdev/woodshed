import SignalsmithStretch from './vendor/SignalsmithStretch.mjs';

const PEAKS_PER_SECOND = 200;
const PLAYHEAD_FRACTION = 0.3;          // playhead sits here; the waveform scrolls under it
const SPEED_STEP = 0.05, MIN_SPEED = 0.25, MAX_SPEED = 1.5;
const STEP_BACK_GRACE_SECONDS = 0.18;   // while playing, ← within this of a beat goes to the one before
const METER_CYCLE = [null, 2, 3, 4, 5, 6, 7];
const ROWS = { barNumbers: 18, labels: 58, waveTop: 84, transientHeight: 12 };

const $ = id => document.getElementById(id);
const timeline = $('timeline'), overview = $('overview'), editor = $('label-editor');
const colors = Object.fromEntries(['bg', 'text', 'dim', 'wave', 'wave-loop', 'beat', 'bar', 'playhead', 'loop', 'label', 'accent']
  .map(name => [name, getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()]));

const state = {
  songId: null, analysis: null, notes: null, duration: 0, peaks: null,
  playing: false, position: 0, playStartPosition: 0,
  reportedTime: 0, reportedAt: 0, latencyCompensation: 0,
  anchorPosition: 0, anchoredAt: 0, lastEngineReport: null, lastWrapAt: -Infinity,
  engineLoop: null, pixelsPerSecond: 90, viewOffset: 0, followingPlayhead: true, editingBeatIndex: null, dragRange: null, markerFlash: null, labelHitboxes: [],
  beats: [], downbeatFlags: [], barNumbers: [], transients: [],
};

// ---------- diagnostics ----------
// Inside the app there is no console to open, so problems are sent to the server's log file.
const reportToLog = message => fetch('/api/log', { method: 'POST', body: JSON.stringify({ message }) }).catch(() => {});
window.addEventListener('error', event => reportToLog(`error: ${event.message} (${event.filename}:${event.lineno})`));
window.addEventListener('unhandledrejection', event => reportToLog(`unhandled rejection: ${event.reason?.stack ?? event.reason}`));

// ---------- audio engine ----------

let audioContext = null, stretch = null;

async function ensureEngine() {
  if (stretch) return;
  audioContext = new AudioContext();
  stretch = await SignalsmithStretch(audioContext);
  stretch.connect(audioContext.destination);
  stretch.setUpdateInterval(0.02, receiveEngineTime);
  state.latencyCompensation = (await stretch.latency()) + (audioContext.outputLatency || 0);
  reportToLog(`audio engine ready: ${audioContext.sampleRate} Hz, context ${audioContext.state}, latency ${state.latencyCompensation.toFixed(3)}s`);
}

function activeLoop() {
  const loop = state.notes?.loop;
  return loop && loop.enabled && loop.end_seconds > loop.start_seconds ? loop : null;
}

// The loop stays armed when the playhead is put beyond its end: playback just carries on from
// there (the engine would otherwise wrap into the loop at an arbitrary phase), and looping
// resumes as soon as the playhead is put back before the loop end.
function applyTransport(extra = {}) {
  const armed = activeLoop(), position = extra.input ?? currentPosition();
  const loop = armed && position < armed.end_seconds ? armed : null;
  state.engineLoop = loop;
  stretch.schedule({
    active: state.playing, rate: state.notes.speed,
    loopStart: loop ? loop.start_seconds : 0, loopEnd: loop ? loop.end_seconds : 0, ...extra,
  });
}

// The engine reports the input time it is *reading*; what is *heard* trails that by the engine's
// latency. currentPosition() subtracts the latency, which needs care in exactly two places:
//  - just after the engine wraps a loop, the corrected time falls before the loop start, but
//    the audio being heard is really the loop's tail;
//  - just after the transport is (re)started somewhere, the corrected time falls before that
//    spot, but nothing earlier than it will ever be heard.
// Telling those apart from "the playhead is simply approaching the loop from the left" takes
// knowing whether a wrap actually happened, so wraps are detected from the reports themselves.
const STALE_REPORT_WINDOW_MILLISECONDS = 60, WRAP_DETECTION_SECONDS = 0.05;

function receiveEngineTime(time) {
  const now = performance.now();
  if (now - state.anchoredAt < STALE_REPORT_WINDOW_MILLISECONDS) return;  // may predate the restart that just happened
  if (state.engineLoop && state.lastEngineReport !== null && time < state.lastEngineReport - WRAP_DETECTION_SECONDS) state.lastWrapAt = now;
  state.lastEngineReport = time;
  state.reportedTime = time; state.reportedAt = now;
}

/** Point the engine at `position` and re-anchor the playhead clock there. */
function restartTransportAt(position) {
  Object.assign(state, { reportedTime: position, reportedAt: performance.now(), anchorPosition: position, anchoredAt: performance.now(), lastEngineReport: null });
  applyTransport({ input: position });
}

function currentPosition() {
  if (!state.playing) return state.position;
  const now = performance.now();
  // reports arrive every 20ms; capping the extrapolation keeps the playhead honest if the engine stalls
  const elapsed = Math.min((now - state.reportedAt) / 1000, 0.1);
  let estimate = state.reportedTime + (elapsed - state.latencyCompensation) * state.notes.speed;
  const loop = state.engineLoop, wrappedSinceAnchor = state.lastWrapAt > state.anchoredAt;
  if (!wrappedSinceAnchor) estimate = Math.max(estimate, state.anchorPosition);
  else if (loop && estimate < loop.start_seconds && (now - state.lastWrapAt) / 1000 < 2 * state.latencyCompensation + 0.1) {
    estimate += loop.end_seconds - loop.start_seconds;
  }
  return Math.min(Math.max(estimate, 0), state.duration);
}

function play() {
  if (!state.notes) return;
  audioContext.resume();
  if (state.position >= state.duration - 0.05) state.position = 0;
  followPlayhead();
  state.playing = true;
  state.playStartPosition = state.position;
  restartTransportAt(state.position);
}

function pause() {
  state.position = currentPosition();
  state.playing = false;
  applyTransport();
}

function seek(time) {
  time = Math.min(Math.max(time, 0), state.duration);
  // keep the waveform where it is for this frame, then glide: jumping the view under the cursor is disorienting
  state.viewOffset += currentPosition() - time;
  followPlayhead();
  state.position = time;
  if (state.playing) {
    state.playStartPosition = time;
    restartTransportAt(time);
  }
}

function setSpeed(speed) {
  const position = currentPosition();
  state.notes.speed = Math.round(Math.min(Math.max(speed, MIN_SPEED), MAX_SPEED) * 100) / 100;
  if (state.playing) restartTransportAt(position);
  saveNotesSoon();
}

// ---------- grid ----------

function rebuildGrid() {
  const analysis = state.analysis;
  state.beats = analysis ? analysis.beat_times : [];
  state.transients = analysis ? analysis.transient_times : [];
  const detectedDownbeatIndexes = analysis ? analysis.downbeat_times.map(time => nearestIndex(state.beats, time)) : [];
  const { beats_per_bar_override: meterOverride, downbeat_anchor_beat_index: anchor } = state.notes;
  let flags;
  if (meterOverride !== null || anchor !== null) {
    const meter = meterOverride ?? mostCommonBarLength(detectedDownbeatIndexes);
    const origin = anchor ?? detectedDownbeatIndexes[0] ?? 0;
    flags = state.beats.map((_, index) => (((index - origin) % meter) + meter) % meter === 0);
  } else {
    const detected = new Set(detectedDownbeatIndexes);
    flags = state.beats.map((_, index) => detected.has(index));
  }
  state.downbeatFlags = flags;
  let bar = 0;
  state.barNumbers = flags.map(isDownbeat => (isDownbeat ? ++bar : bar));
}

function mostCommonBarLength(downbeatIndexes) {
  const counts = new Map();
  for (let i = 1; i < downbeatIndexes.length; i++) {
    const length = downbeatIndexes[i] - downbeatIndexes[i - 1];
    counts.set(length, (counts.get(length) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? 4;
}

function nearestIndex(times, time) {
  let best = 0;
  for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - time) < Math.abs(times[best] - time)) best = i;
  return best;
}

function gridTimes(unit) {
  if (unit === 'transient') return state.transients;
  if (unit === 'bar') return state.beats.filter((_, index) => state.downbeatFlags[index]);
  return state.beats;
}

const unitFromEvent = event => (event.altKey ? 'transient' : event.shiftKey ? 'bar' : 'beat');

function snap(time, unit) {
  const times = gridTimes(unit);
  return times.length ? times[nearestIndex(times, time)] : time;
}

function step(direction, unit) {
  const times = gridTimes(unit), position = currentPosition();
  if (!times.length) return seek(position + direction * 2);
  if (direction < 0) {
    const grace = state.playing ? STEP_BACK_GRACE_SECONDS * state.notes.speed : 0.01;
    const earlier = times.filter(time => time < position - grace);
    seek(earlier.length ? earlier[earlier.length - 1] : 0);
  } else {
    const later = times.find(time => time > position + 0.01);
    if (later !== undefined) seek(later);
  }
}

function beatIndexAt(time) {
  let index = -1;
  for (let i = 0; i < state.beats.length && state.beats[i] <= time + 0.01; i++) index = i;
  return index;
}

// ---------- loop ----------

function setLoopPoint(edge, unit) {
  const time = snap(currentPosition(), unit);
  const loop = state.notes.loop ?? { start_seconds: 0, end_seconds: 0, enabled: false };
  if (edge === 'start') {
    loop.start_seconds = time;
    if (loop.end_seconds <= time) loop.end_seconds = time;
  } else {
    if (time <= loop.start_seconds) return setStatus('the loop end has to come after the loop start');
    loop.end_seconds = time;
  }
  loop.enabled = loop.end_seconds > loop.start_seconds;
  state.markerFlash = { edge, startedAt: performance.now() };
  setStatus(loop.enabled ? '' : 'loop start set — press ] where the loop should end');
  commitLoop(loop, edge === 'end');
}

function commitLoop(loop, restart) {
  state.notes.loop = loop;
  const position = currentPosition();
  // enabling a loop from beyond its end would make the engine wrap to an arbitrary phase; restart cleanly instead
  if (loop.enabled && (restart || position >= loop.end_seconds)) {
    state.viewOffset += position - loop.start_seconds;
    followPlayhead();
    state.position = loop.start_seconds;
    if (state.playing) restartTransportAt(loop.start_seconds);
  } else if (state.playing) {
    restartTransportAt(position);
  }
  saveNotesSoon();
}

function toggleLoop() {
  const loop = state.notes.loop;
  if (!loop || loop.end_seconds <= loop.start_seconds) return;
  loop.enabled = !loop.enabled;
  commitLoop(loop, false);
}

function reshapeLoop({ shiftBy = 0, scaleBy = 1 }) {
  const loop = state.notes.loop;
  if (!loop || loop.end_seconds <= loop.start_seconds || !state.beats.length) return;
  let startIndex = nearestIndex(state.beats, loop.start_seconds);
  let lengthInBeats = Math.max(1, nearestIndex(state.beats, loop.end_seconds) - startIndex);
  startIndex += shiftBy * lengthInBeats;
  lengthInBeats = Math.max(1, Math.round(lengthInBeats * scaleBy));
  if (startIndex < 0 || startIndex + lengthInBeats >= state.beats.length) return;
  commitLoop({ start_seconds: state.beats[startIndex], end_seconds: state.beats[startIndex + lengthInBeats], enabled: true }, true);
}

// ---------- labels ----------

const labelKey = beatIndex => String(state.beats[beatIndex]);

function openEditor(beatIndex, prefill = null) {
  if (beatIndex < 0 || beatIndex >= state.beats.length) return;
  state.editingBeatIndex = beatIndex;
  editor.value = prefill ?? state.notes.labels[labelKey(beatIndex)] ?? '';
  editor.hidden = false;
  editor.focus();
  if (prefill === null) editor.select();
}

function closeEditor(commit) {
  if (state.editingBeatIndex === null) return;
  if (commit) {
    const text = editor.value.trim(), key = labelKey(state.editingBeatIndex);
    if (text) state.notes.labels[key] = text; else delete state.notes.labels[key];
    saveNotesSoon();
  }
  state.editingBeatIndex = null;
  editor.hidden = true;
  editor.blur();
}

function editNeighbour(direction, unit) {
  let index = state.editingBeatIndex + direction;
  if (unit === 'bar') while (index >= 0 && index < state.beats.length && !state.downbeatFlags[index]) index += direction;
  if (index < 0 || index >= state.beats.length) return;
  closeEditor(true);
  if (!state.playing) seek(state.beats[index]);
  openEditor(index);
}

editor.addEventListener('keydown', event => {
  event.stopPropagation();
  if (event.key === 'Enter') { event.preventDefault(); closeEditor(true); }
  else if (event.key === 'Escape') { event.preventDefault(); closeEditor(false); }
  else if (event.key === 'Tab') { event.preventDefault(); editNeighbour(event.shiftKey ? -1 : 1, 'bar'); }
  else if (event.altKey && (event.key === 'ArrowRight' || event.key === 'ArrowLeft')) { event.preventDefault(); editNeighbour(event.key === 'ArrowRight' ? 1 : -1, 'beat'); }
});
editor.addEventListener('blur', () => closeEditor(true));

// ---------- persistence & loading ----------

let saveTimer = null;
// ---------- undo ----------
// Every edit funnels through saveNotesSoon(), so that is where history is recorded. Speed and
// key are left out: undo is for labels, loops and bar fixes, not for nudging a slider back.

const history = { undo: [], redo: [], current: null };
const undoableSnapshot = () => { const { speed, key, position_seconds, ...rest } = state.notes; return JSON.stringify(rest); };

function resetHistory() { Object.assign(history, { undo: [], redo: [], current: undoableSnapshot() }); }

function recordHistory() {
  const snapshot = undoableSnapshot();
  if (snapshot === history.current) return;
  history.undo.push(history.current); history.redo = []; history.current = snapshot;
}

function travelHistory(from, to, verb) {
  if (!from.length) return setStatus(`nothing to ${verb}`);
  const position = currentPosition();
  to.push(history.current);
  history.current = from.pop();
  Object.assign(state.notes, JSON.parse(history.current));
  rebuildGrid();
  if (state.playing) restartTransportAt(position);
  saveNotesSoon({ record: false });
  setStatus(`${verb} ✓`);
}

let pendingSave = null;
function saveNotesSoon({ record = true } = {}) {
  if (record) recordHistory();
  clearTimeout(saveTimer);
  const songId = state.songId, notes = state.notes;
  pendingSave = () => {
    pendingSave = null;
    fetch(`/api/songs/${encodeURIComponent(songId)}/notes`, { method: 'PUT', body: JSON.stringify(notes), keepalive: true })
      .then(response => { if (!response.ok) setStatus('⚠ could not save notes'); });
  };
  saveTimer = setTimeout(() => pendingSave?.(), 300);
}

// The playhead position is remembered by one mechanism only: once a second, if it has moved, it
// is written into the notes. That covers pausing, quitting and crashing alike (at most a second
// stale) with no per-event hooks. Switching songs flushes, because the debounced save above
// would otherwise be cancelled by the next song's first save.
const POSITION_SAVE_INTERVAL_MILLISECONDS = 1000;

function rememberPosition() {
  if (!state.notes) return;
  const position = Math.round(currentPosition() * 100) / 100;
  if (position === state.notes.position_seconds) return;
  state.notes.position_seconds = position;
  saveNotesSoon({ record: false });
}
setInterval(rememberPosition, POSITION_SAVE_INTERVAL_MILLISECONDS);

function flushNotes() {
  rememberPosition();
  clearTimeout(saveTimer);
  pendingSave?.();
}

const setStatus = text => { $('status').textContent = text; };

async function loadSong(songId, startSeconds = null) {
  if (state.playing) pause();
  closeEditor(true);
  flushNotes();
  setStatus('loading…');
  await ensureEngine();
  const song = await (await fetch(`/api/songs/${encodeURIComponent(songId)}`)).json();
  if (song.error) return setStatus(song.error);
  const encoded = await (await fetch(`/audio/${encodeURIComponent(songId)}`)).arrayBuffer();
  const buffer = await audioContext.decodeAudioData(encoded);
  const left = buffer.getChannelData(0), right = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : left;
  await stretch.dropBuffers();
  await stretch.addBuffers([left, right]);

  Object.assign(state, { songId, analysis: song.analysis, notes: song.notes, duration: buffer.duration, peaks: computePeaks(left, right, buffer.sampleRate), position: 0 });
  rebuildGrid();
  resetHistory();
  seek(startSeconds ?? song.notes.position_seconds);  // an explicit start (Spotify's position on a grab) beats the remembered one
  state.viewOffset = 0;
  applyTransport({ input: state.position });
  $('title').textContent = songId;
  $('key').value = song.notes.key;
  document.title = `${songId} — Woodshed`;
  setStatus(song.analysis ? '' : 'finding the beat…');
  if (!song.analysis) pollForAnalysis(songId);
}

async function pollForAnalysis(songId) {
  while (state.songId === songId && !state.analysis) {
    await new Promise(resolve => setTimeout(resolve, 1500));
    const song = await (await fetch(`/api/songs/${encodeURIComponent(songId)}`)).json();
    if (state.songId !== songId) return;
    if (song.analysis_error) return setStatus(`⚠ beat analysis failed: ${song.analysis_error}`);
    if (song.analysis) { state.analysis = song.analysis; rebuildGrid(); setStatus(''); }
  }
}

function computePeaks(left, right, sampleRate) {
  const samplesPerPeak = sampleRate / PEAKS_PER_SECOND, count = Math.ceil(left.length / samplesPerPeak);
  const peaks = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    let max = 0;
    const end = Math.min(left.length, Math.floor((i + 1) * samplesPerPeak));
    for (let s = Math.floor(i * samplesPerPeak); s < end; s++) max = Math.max(max, Math.abs(left[s]), Math.abs(right[s]));
    peaks[i] = max;
  }
  return peaks;
}

async function grab(url = null) {
  setStatus(url ? 'fetching…' : 'grabbing from Spotify…');
  const response = await fetch('/api/grab', { method: 'POST', body: JSON.stringify({ url }) });
  const grabbed = await response.json();
  if (!response.ok) { reportToLog(`grab failed: ${grabbed.error}`); return setStatus(`⚠ ${grabbed.error}`); }
  reportToLog(`grabbed: ${grabbed.song_id}`);
  location.hash = `song=${encodeURIComponent(grabbed.song_id)}&t=${grabbed.position_seconds}`;
}

async function openSongList() {
  const ids = await (await fetch('/api/songs')).json();
  $('song-list').replaceChildren(...ids.map(id => {
    const item = document.createElement('li');
    item.textContent = id;
    item.onclick = () => { $('songs').close(); location.hash = `song=${encodeURIComponent(id)}`; };
    return item;
  }));
  $('songs').showModal();
}

async function route() {
  const parameters = new URLSearchParams(location.hash.slice(1));
  const songId = parameters.get('song');
  if (songId) return loadSong(songId, Number(parameters.get('t')) || null);
  const ids = await (await fetch('/api/songs')).json();
  if (ids.length) location.hash = `song=${encodeURIComponent(ids[0])}`; else openSongList();
}

// ---------- drawing ----------

function sizeCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1, width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

// Sidescrolling detaches the view from the playhead (viewOffset seconds away from it) without
// touching playback; anything that moves or starts the playhead glides the view back.
const viewStart = (position, width) => position + state.viewOffset - (PLAYHEAD_FRACTION * width) / state.pixelsPerSecond;
const followPlayhead = () => { state.followingPlayhead = true; };

function scrollView(seconds) {
  const position = currentPosition();
  state.followingPlayhead = false;
  state.viewOffset = Math.min(Math.max(state.viewOffset + seconds, -position), state.duration - position);
}

function peakBetween(startTime, endTime) {
  const first = Math.max(0, Math.floor(startTime * PEAKS_PER_SECOND)), last = Math.min(state.peaks.length, Math.ceil(endTime * PEAKS_PER_SECOND));
  let max = 0;
  for (let i = first; i < last; i++) if (state.peaks[i] > max) max = state.peaks[i];
  return max;
}

function drawTimeline(position) {
  const { context, width, height } = sizeCanvas(timeline);
  context.fillStyle = colors.bg; context.fillRect(0, 0, width, height);
  if (!state.peaks) return;
  const start = viewStart(position, width), pps = state.pixelsPerSecond, xOf = time => (time - start) * pps;
  const waveTop = ROWS.waveTop, waveBottom = height - ROWS.transientHeight - 8, middle = (waveTop + waveBottom) / 2, amplitude = (waveBottom - waveTop) / 2;
  const loop = state.notes.loop, region = state.dragRange ?? (loop && loop.end_seconds > loop.start_seconds ? loop : null);

  if (region) {
    const [from, to] = state.dragRange ? [region.from, region.to] : [region.start_seconds, region.end_seconds];
    const on = state.dragRange || loop.enabled;
    context.fillStyle = colors.loop; context.globalAlpha = on ? 1 : 0.45;
    context.fillRect(xOf(from), 0, (to - from) * pps, height);
    context.fillStyle = colors.accent; context.globalAlpha = on ? 1 : 0.5;
    context.fillRect(xOf(from), 0, 2, height); context.fillRect(xOf(to) - 2, 0, 2, height);
    context.fillRect(xOf(from), 0, (to - from) * pps, 3);
    context.globalAlpha = 1;
  }

  state.beats.forEach((time, index) => {
    const x = xOf(time);
    if (x < -50 || x > width + 50) return;
    const isDownbeat = state.downbeatFlags[index];
    context.fillStyle = isDownbeat ? colors.bar : colors.beat;
    context.fillRect(Math.round(x), isDownbeat ? 0 : waveTop - 8, isDownbeat ? 2 : 1, height);
    if (isDownbeat) { context.fillStyle = colors.dim; context.font = '11px -apple-system, sans-serif'; context.fillText(String(state.barNumbers[index]), x + 5, ROWS.barNumbers); }
  });

  const loopOn = activeLoop();
  for (let x = 0; x < width; x++) {
    const time = start + x / pps;
    if (time < 0 || time > state.duration) continue;
    const peak = peakBetween(time, time + 1 / pps) * amplitude;
    context.fillStyle = loopOn && time >= loopOn.start_seconds && time < loopOn.end_seconds ? colors['wave-loop'] : colors.wave;
    context.fillRect(x, middle - peak, 1, Math.max(1, peak * 2));
  }

  context.fillStyle = colors.dim;
  for (const time of state.transients) { const x = xOf(time); if (x >= 0 && x <= width) context.fillRect(Math.round(x), height - ROWS.transientHeight, 1, ROWS.transientHeight); }

  context.textBaseline = 'alphabetic';
  state.labelHitboxes = [];
  for (const [key, text] of Object.entries(state.notes.labels)) {
    const time = Number(key), x = xOf(time);
    if (x < -200 || x > width) continue;
    const beatIndex = state.beats.indexOf(time), onDownbeat = state.downbeatFlags[beatIndex];
    context.font = `600 ${onDownbeat ? 26 : 18}px "SF Mono", ui-monospace, monospace`;
    context.fillStyle = colors.label;
    context.fillText(text, x + 5, ROWS.labels);
    state.labelHitboxes.push({ beatIndex, from: x, to: x + 12 + context.measureText(text).width });
  }

  drawLoopMarkers(context, xOf, height);

  const playheadX = xOf(position);
  context.fillStyle = colors.playhead; context.fillRect(Math.round(playheadX) - 1, 0, 2, height);

  if (state.editingBeatIndex !== null) {
    const x = Math.min(Math.max(xOf(state.beats[state.editingBeatIndex]) + 3, 4), width - 120);
    editor.style.left = `${x}px`;
  }
}

// Locator flags for the loop's two ends. They are drawn for a lone start marker too, so the
// first [ gives feedback before there is any loop region to show, and a just-placed marker
// flashes briefly.
const MARKER_FLASH_MILLISECONDS = 700, MARKER_FLAG_SIZE = 13;

function loopMarkers() {
  const loop = state.notes?.loop;
  if (!loop) return [];
  const markers = [{ edge: 'start', time: loop.start_seconds }];
  if (loop.end_seconds > loop.start_seconds) markers.push({ edge: 'end', time: loop.end_seconds });
  return markers;
}

function drawLoopMarkers(context, xOf, height) {
  const dimmed = state.notes.loop && state.notes.loop.end_seconds > state.notes.loop.start_seconds && !state.notes.loop.enabled;
  for (const { edge, time } of loopMarkers()) {
    const x = Math.round(xOf(time)), inward = edge === 'start' ? 1 : -1;
    const flashAge = state.markerFlash?.edge === edge ? performance.now() - state.markerFlash.startedAt : Infinity;
    if (flashAge < MARKER_FLASH_MILLISECONDS) {
      const fade = 1 - flashAge / MARKER_FLASH_MILLISECONDS;
      context.fillStyle = colors.accent; context.globalAlpha = 0.45 * fade;
      context.fillRect(x - 9 * fade - 1, 0, 18 * fade + 2, height);
    }
    context.fillStyle = colors.accent; context.globalAlpha = dimmed ? 0.55 : 1;
    context.fillRect(x - 1, 0, 2, height);
    for (const [y, direction] of [[0, 1], [height, -1]]) {  // a flag at the top and one at the bottom, pointing into the loop
      context.beginPath();
      context.moveTo(x, y); context.lineTo(x + inward * MARKER_FLAG_SIZE, y); context.lineTo(x, y + direction * MARKER_FLAG_SIZE);
      context.closePath(); context.fill();
    }
    context.globalAlpha = 1;
  }
}

function drawOverview(position) {
  const { context, width, height } = sizeCanvas(overview);
  context.fillStyle = '#101214'; context.fillRect(0, 0, width, height);
  if (!state.peaks) return;
  const secondsPerPixel = state.duration / width, loop = state.notes.loop;
  if (loop && loop.end_seconds > loop.start_seconds) {
    const x = loop.start_seconds / secondsPerPixel, regionWidth = Math.max(3, (loop.end_seconds - loop.start_seconds) / secondsPerPixel);
    context.fillStyle = colors.accent; context.globalAlpha = loop.enabled ? 0.55 : 0.22;
    context.fillRect(x, 0, regionWidth, height);
    context.globalAlpha = loop.enabled ? 1 : 0.6;
    context.fillRect(x, 0, regionWidth, 3); context.fillRect(x, height - 3, regionWidth, 3);
    context.globalAlpha = 1;
  }
  context.fillStyle = colors.wave;
  for (let x = 0; x < width; x++) {
    const peak = peakBetween(x * secondsPerPixel, (x + 1) * secondsPerPixel) * (height / 2 - 4);
    context.fillRect(x, height / 2 - peak, 1, Math.max(1, peak * 2));
  }
  context.fillStyle = colors.accent;
  for (const { time } of loopMarkers()) context.fillRect(Math.round(time / secondsPerPixel) - 1, 0, 2, height);
  context.fillStyle = colors.label;
  for (const key of Object.keys(state.notes.labels)) context.fillRect(Number(key) / secondsPerPixel, height - 5, 2, 5);
  const viewWidth = timeline.clientWidth / state.pixelsPerSecond;
  context.strokeStyle = colors.bar; context.strokeRect(viewStart(position, timeline.clientWidth) / secondsPerPixel, 0.5, viewWidth / secondsPerPixel, height - 1);
  context.fillStyle = colors.playhead; context.fillRect(position / secondsPerPixel - 1, 0, 2, height);
}

function updateReadouts(position) {
  const index = beatIndexAt(position), speed = state.notes?.speed ?? 1, loop = state.notes?.loop;
  let barText = 'bar –';
  if (index >= 0 && state.barNumbers[index] > 0) {
    let beatInBar = 1;
    for (let i = index; i >= 0 && !state.downbeatFlags[i]; i--) beatInBar++;
    barText = `bar ${state.barNumbers[index]}.${beatInBar}`;
  }
  const minutes = Math.floor(position / 60), seconds = Math.floor(position % 60);
  $('position-readout').textContent = `${barText}  ${minutes}:${String(seconds).padStart(2, '0')}`;
  $('speed-readout').textContent = `${Math.round(speed * 100)}%`;
  $('speed-readout').classList.toggle('slowed', speed !== 1);
  const hasLoop = loop && loop.end_seconds > loop.start_seconds;
  $('loop-readout').hidden = !hasLoop;
  $('loop-readout').textContent = hasLoop && loop.enabled ? '⟲ looping' : '⟲ loop off';
  $('loop-readout').classList.toggle('off', !(hasLoop && loop.enabled));
}

function frame() {
  const position = currentPosition();
  if (state.followingPlayhead && state.viewOffset !== 0) state.viewOffset = Math.abs(state.viewOffset) < 0.005 ? 0 : state.viewOffset * 0.8;
  if (state.playing && position >= state.duration - 0.02 && !state.engineLoop) { pause(); state.position = state.duration; }
  drawTimeline(position); drawOverview(position); updateReadouts(position);
  requestAnimationFrame(frame);
}

// ---------- input ----------

const timeAtTimelineX = x => viewStart(currentPosition(), timeline.clientWidth) + x / state.pixelsPerSecond;

const LABEL_ROW = { top: ROWS.labels - 30, bottom: ROWS.labels + 10 };
const labelUnderPointer = event => (event.offsetY < LABEL_ROW.top || event.offsetY > LABEL_ROW.bottom ? null
  : state.labelHitboxes.find(box => box.beatIndex >= 0 && event.offsetX >= box.from && event.offsetX <= box.to) ?? null);
timeline.addEventListener('mousemove', event => { timeline.style.cursor = labelUnderPointer(event) ? 'text' : ''; });

function deleteLabelAtPlayhead() {
  const beatIndex = beatIndexAt(currentPosition());
  if (beatIndex < 0 || !(labelKey(beatIndex) in state.notes.labels)) return setStatus('no label on this beat');
  delete state.notes.labels[labelKey(beatIndex)];
  saveNotesSoon();
  setStatus('label deleted (⌘Z to undo)');
}

timeline.addEventListener('mousedown', down => {
  if (!state.notes) return;
  closeEditor(true);
  const label = labelUnderPointer(down);
  if (label) { down.preventDefault(); return openEditor(label.beatIndex); }  // preventDefault keeps focus in the editor
  const startTime = timeAtTimelineX(down.offsetX), startX = down.clientX, rectangle = timeline.getBoundingClientRect();
  const move = event => {
    if (Math.abs(event.clientX - startX) < 5) return;
    const unit = unitFromEvent(event), here = timeAtTimelineX(event.clientX - rectangle.left);
    state.dragRange = { from: snap(Math.min(startTime, here), unit), to: snap(Math.max(startTime, here), unit) };
  };
  const up = event => {
    window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
    const range = state.dragRange; state.dragRange = null;
    if (range && range.to > range.from) commitLoop({ start_seconds: range.from, end_seconds: range.to, enabled: true }, true);
    else if (!range) seek(snap(startTime, unitFromEvent(event)));
  };
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
});

// Trackpad pinch: Chrome and Firefox deliver it as ctrl+wheel, Safari as gesture events.
const MIN_ZOOM = 10, MAX_ZOOM = 600;
const setZoom = pixelsPerSecond => { state.pixelsPerSecond = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, pixelsPerSecond)); };
for (const canvas of [timeline, overview]) {
  canvas.addEventListener('wheel', event => {
    event.preventDefault();  // otherwise a pinch zooms the whole page and a sideways swipe goes "back"
    if (event.ctrlKey) setZoom(state.pixelsPerSecond * Math.exp(-event.deltaY * 0.01));
    else if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) scrollView(event.deltaX / state.pixelsPerSecond);
  }, { passive: false });
  let zoomAtGestureStart = 0;
  canvas.addEventListener('gesturestart', event => { event.preventDefault(); zoomAtGestureStart = state.pixelsPerSecond; });
  canvas.addEventListener('gesturechange', event => { event.preventDefault(); setZoom(zoomAtGestureStart * event.scale); });
}

overview.addEventListener('mousedown', event => { if (state.notes) seek(snap((event.offsetX / overview.clientWidth) * state.duration, 'beat')); });

$('title').onclick = openSongList;
$('loop-readout').onclick = toggleLoop;
$('key').addEventListener('input', () => { state.notes.key = $('key').value.trim(); saveNotesSoon(); });
$('key').addEventListener('keydown', event => { event.stopPropagation(); if (event.key === 'Enter' || event.key === 'Escape') $('key').blur(); });
$('url-form').addEventListener('submit', event => { event.preventDefault(); $('songs').close(); grab($('url').value.trim()); $('url').value = ''; });
$('url').addEventListener('keydown', event => event.stopPropagation());

window.addEventListener('keydown', event => {
  if (!state.notes || $('songs').open) return;
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    return event.shiftKey ? travelHistory(history.redo, history.undo, 'redo') : travelHistory(history.undo, history.redo, 'undo');
  }
  if (event.metaKey || event.ctrlKey) return;
  // Tab only means something inside the label editor; here it would move focus into the key
  // field, which then swallows every shortcut typed after it.
  if (event.key === 'Tab') return event.preventDefault();
  const unit = unitFromEvent(event), key = event.key;
  const handlers = {
    ' ': () => (state.playing ? pause() : play()),
    ArrowLeft: () => step(-1, unit), ArrowRight: () => step(1, unit),
    ArrowUp: () => setZoom(state.pixelsPerSecond * 1.25),
    ArrowDown: () => setZoom(state.pixelsPerSecond / 1.25),
    l: toggleLoop,
    ',': () => reshapeLoop({ shiftBy: -1 }), '.': () => reshapeLoop({ shiftBy: 1 }),
    '<': () => reshapeLoop({ scaleBy: 0.5 }), '>': () => reshapeLoop({ scaleBy: 2 }),
    r: () => seek(activeLoop()?.start_seconds ?? state.playStartPosition),
    '-': () => setSpeed(state.notes.speed - SPEED_STEP), '=': () => setSpeed(state.notes.speed + SPEED_STEP), '0': () => setSpeed(1),
    Enter: () => openEditor(beatIndexAt(currentPosition())),
    Backspace: deleteLabelAtPlayhead, Delete: deleteLabelAtPlayhead,
    d: () => { state.notes.downbeat_anchor_beat_index = Math.max(0, beatIndexAt(currentPosition())); rebuildGrid(); saveNotesSoon(); },
    m: () => {
      const next = METER_CYCLE[(METER_CYCLE.indexOf(state.notes.beats_per_bar_override) + 1) % METER_CYCLE.length];
      state.notes.beats_per_bar_override = next;
      if (next === null) state.notes.downbeat_anchor_beat_index = null;
      rebuildGrid(); saveNotesSoon();
      setStatus(next ? `${next} beats per bar` : 'detected bars');
    },
    g: () => grab(), o: openSongList,
  };
  // brackets go by physical key: with ⌥ or ⇧ held, event.key is a curly quote or a brace
  if (event.code === 'BracketLeft' || event.code === 'BracketRight') { event.preventDefault(); return setLoopPoint(event.code === 'BracketLeft' ? 'start' : 'end', unit); }
  if (/^[1-7b#]$/.test(key) && !event.altKey) { event.preventDefault(); return openEditor(beatIndexAt(currentPosition()), key); }
  const handler = handlers[key];
  if (handler) { event.preventDefault(); handler(); }
});

// the native app shell drives grab/openSongList from its menu, URL scheme and global hotkey; the rest is for the console
window.woodshed = { state, currentPosition, grab, openSongList, engine: () => ({ audioContext, stretch }) };
window.addEventListener('hashchange', route);
requestAnimationFrame(frame);
route();
