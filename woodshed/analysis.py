"""Beat, downbeat and transient detection for one audio file.

Runs the beat_this model (Foscarin, Schlüter & Widmer, 2024) through ONNX Runtime with the
surrounding signal processing reimplemented in NumPy, so the app ships without PyTorch.
scripts/check_analysis_parity.py compares this pipeline against the reference implementation.
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from functools import cache
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from woodshed.library import Analysis
from woodshed.resources import MODEL_PATH

FloatArray = NDArray[np.float32]


SAMPLE_RATE = 22050
FFT_SIZE = 1024
HOP_SAMPLES = 441
FRAMES_PER_SECOND = SAMPLE_RATE / HOP_SAMPLES  # 50
MEL_BANDS = 128
MEL_MIN_HZ, MEL_MAX_HZ = 30.0, 11000.0
LOG_MULTIPLIER = 1000.0

CHUNK_FRAMES = 1500  # fixed by the exported model
BORDER_FRAMES = 6    # predictions this close to a chunk edge are unreliable and discarded
PEAK_NEIGHBOURHOOD_FRAMES = 3
INFERENCE_THREADS = 4  # measured on Apple Silicon: more threads spill onto efficiency cores and run slower


# ---------- decoding ----------

def decode_to_mono(path: Path) -> FloatArray:
    """Decode with macOS's own afconvert, so the app needs no ffmpeg."""
    with tempfile.TemporaryDirectory() as directory:
        wav_path = Path(directory) / "decoded.wav"
        result = subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", f"LEF32@{SAMPLE_RATE}", "-c", "1", str(path), str(wav_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"afconvert could not decode {path.name}: {result.stderr.strip()}")
        return read_float32_wav(wav_path.read_bytes())


def read_float32_wav(data: bytes) -> FloatArray:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    offset = 12
    while offset + 8 <= len(data):
        chunk_id, chunk_size = data[offset:offset + 4], struct.unpack("<I", data[offset + 4:offset + 8])[0]
        if chunk_id == b"data":
            return np.frombuffer(data, dtype="<f4", count=chunk_size // 4, offset=offset + 8).astype(np.float32)
        offset += 8 + chunk_size + (chunk_size & 1)
    raise ValueError("WAV file has no data chunk")


# ---------- log-mel spectrogram (matches torchaudio's MelSpectrogram as beat_this configures it) ----------

def _hz_to_mel_slaney(hz: NDArray[np.float64]) -> NDArray[np.float64]:
    linear_mel = hz / (200.0 / 3)
    log_region_start_hz, log_step = 1000.0, np.log(6.4) / 27.0
    log_mel = 15.0 + np.log(np.maximum(hz, 1e-10) / log_region_start_hz) / log_step
    return np.where(hz >= log_region_start_hz, log_mel, linear_mel)


def _mel_to_hz_slaney(mel: NDArray[np.float64]) -> NDArray[np.float64]:
    log_step = np.log(6.4) / 27.0
    return np.where(mel >= 15.0, 1000.0 * np.exp(log_step * (mel - 15.0)), mel * (200.0 / 3))


@cache
def mel_filterbank() -> NDArray[np.float64]:
    """Triangular filters, shape (fft bins, mel bands), unnormalised like torchaudio's norm=None."""
    fft_frequencies = np.linspace(0, SAMPLE_RATE / 2, FFT_SIZE // 2 + 1)
    mel_low, mel_high = _hz_to_mel_slaney(np.array([MEL_MIN_HZ, MEL_MAX_HZ]))
    edge_frequencies = _mel_to_hz_slaney(np.linspace(mel_low, mel_high, MEL_BANDS + 2))
    widths = np.diff(edge_frequencies)
    slopes = edge_frequencies[np.newaxis, :] - fft_frequencies[:, np.newaxis]
    rising = -slopes[:, :-2] / widths[:-1]
    falling = slopes[:, 2:] / widths[1:]
    return np.maximum(0.0, np.minimum(rising, falling))


def log_mel_spectrogram(signal: FloatArray) -> FloatArray:
    """Shape (frames, mel bands)."""
    padded = np.pad(signal.astype(np.float64), FFT_SIZE // 2, mode="reflect")
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(FFT_SIZE) / FFT_SIZE)  # periodic Hann
    frames = np.lib.stride_tricks.sliding_window_view(padded, FFT_SIZE)[::HOP_SAMPLES]
    magnitudes = np.abs(np.fft.rfft(frames * window, axis=1)) / np.sqrt(FFT_SIZE)
    return np.log1p(LOG_MULTIPLIER * (magnitudes @ mel_filterbank())).astype(np.float32)


# ---------- beat model ----------

@cache
def _beat_model():
    import onnxruntime  # slow import; keep it off the startup path

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"beat model missing: {MODEL_PATH} (run scripts/export_beat_model.py)")
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = INFERENCE_THREADS
    return onnxruntime.InferenceSession(str(MODEL_PATH), options, providers=["CPUExecutionProvider"])


def warm_up() -> None:
    _beat_model()


def predict_beat_logits(spectrogram: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Run the model over overlapping chunks and stitch the framewise logits back together."""
    frame_count = len(spectrogram)
    stride = CHUNK_FRAMES - 2 * BORDER_FRAMES
    starts = list(range(-BORDER_FRAMES, frame_count - BORDER_FRAMES, stride))
    if frame_count > stride:
        starts[-1] = frame_count - (CHUNK_FRAMES - BORDER_FRAMES)  # last chunk ends with the piece rather than running short
    beat_logits = np.full(frame_count, -1000.0, dtype=np.float32)
    downbeat_logits = np.full(frame_count, -1000.0, dtype=np.float32)
    # Later chunks are written first so that, where chunks overlap, the earlier chunk wins.
    for start in reversed(starts):
        chunk = np.zeros((CHUNK_FRAMES, MEL_BANDS), dtype=np.float32)
        source_from, source_to = max(start, 0), min(start + CHUNK_FRAMES, frame_count)
        chunk[source_from - start:source_to - start] = spectrogram[source_from:source_to]
        beat, downbeat = _beat_model().run(None, {"spectrogram": chunk[np.newaxis]})
        keep_from, keep_to = start + BORDER_FRAMES, min(start + CHUNK_FRAMES - BORDER_FRAMES, frame_count)
        beat_logits[keep_from:keep_to] = beat[0, BORDER_FRAMES:BORDER_FRAMES + keep_to - keep_from]
        downbeat_logits[keep_from:keep_to] = downbeat[0, BORDER_FRAMES:BORDER_FRAMES + keep_to - keep_from]
    return beat_logits, downbeat_logits


def pick_peak_frames(logits: FloatArray) -> NDArray[np.int64]:
    """Frames that are positive local maxima, with runs of adjacent peaks merged to their mean."""
    padded = np.pad(logits, PEAK_NEIGHBOURHOOD_FRAMES, constant_values=-np.inf)
    neighbourhood_max = np.lib.stride_tricks.sliding_window_view(padded, 2 * PEAK_NEIGHBOURHOOD_FRAMES + 1).max(axis=1)
    peaks = np.flatnonzero((logits == neighbourhood_max) & (logits > 0))
    if len(peaks) == 0:
        return peaks
    runs = np.split(peaks, np.flatnonzero(np.diff(peaks) > 1) + 1)
    return np.array([int(round(run.mean())) for run in runs], dtype=np.int64)


def beats_from_logits(beat_logits: FloatArray, downbeat_logits: FloatArray) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    beat_times = pick_peak_frames(beat_logits) / FRAMES_PER_SECOND
    downbeat_times = pick_peak_frames(downbeat_logits) / FRAMES_PER_SECOND
    if len(beat_times):  # a downbeat is always also a beat: snap each to its nearest beat
        downbeat_times = np.unique([beat_times[np.argmin(np.abs(beat_times - time))] for time in downbeat_times])
    return beat_times, downbeat_times


# ---------- transients ----------

ONSET_DELTA = 0.07                      # how far above the local average an onset must rise (envelope is scaled to 0..1)
ONSET_AVERAGE_FRAMES = 5                # 100 ms either side
ONSET_MIN_GAP_FRAMES = 2
REFINE_BEFORE_SECONDS, REFINE_AFTER_SECONDS = 0.04, 0.02
ATTACK_START_FRACTION = 0.1             # the transient "starts" where the attack has reached this much of its peak


def detect_transients(signal: FloatArray, spectrogram: FloatArray) -> NDArray[np.float64]:
    """Spectral-flux onsets at frame resolution, each then walked back to the start of its attack
    in the waveform so a loop boundary placed there doesn't clip the note."""
    flux = np.maximum(0.0, np.diff(spectrogram, axis=0, prepend=spectrogram[:1])).mean(axis=1)
    if flux.max() <= 0:
        return np.array([])
    envelope = flux / flux.max()
    onset_frames: list[int] = []
    for frame in range(1, len(envelope) - 1):
        window = envelope[max(0, frame - ONSET_AVERAGE_FRAMES):frame + ONSET_AVERAGE_FRAMES + 1]
        is_peak = envelope[frame] >= envelope[frame - 1] and envelope[frame] > envelope[frame + 1]
        far_enough = not onset_frames or frame - onset_frames[-1] > ONSET_MIN_GAP_FRAMES
        if is_peak and far_enough and envelope[frame] >= window.mean() + ONSET_DELTA:
            onset_frames.append(frame)
    times = [refine_to_attack_start(signal, frame / FRAMES_PER_SECOND) for frame in onset_frames]
    return np.unique(np.round(times, 4))


def refine_to_attack_start(signal: FloatArray, rough_time: float) -> float:
    start = max(0, int((rough_time - REFINE_BEFORE_SECONDS) * SAMPLE_RATE))
    end = min(len(signal), int((rough_time + REFINE_AFTER_SECONDS) * SAMPLE_RATE))
    smoothing = int(0.002 * SAMPLE_RATE)
    if end - start <= smoothing:
        return rough_time
    envelope = np.convolve(np.abs(signal[start:end]), np.ones(smoothing) / smoothing, mode="same")
    peak = int(np.argmax(envelope))
    floor = envelope[:peak + 1].min()
    threshold = floor + ATTACK_START_FRACTION * (envelope[peak] - floor)
    below = np.flatnonzero(envelope[:peak + 1] <= threshold)
    attack_start = int(below[-1]) if len(below) else 0
    return (start + attack_start) / SAMPLE_RATE


# ---------- entry point ----------

def analyze_audio_file(path: Path) -> Analysis:
    signal = decode_to_mono(path)
    spectrogram = log_mel_spectrogram(signal)
    beat_times, downbeat_times = beats_from_logits(*predict_beat_logits(spectrogram))
    return Analysis(
        duration_seconds=len(signal) / SAMPLE_RATE,
        beat_times=[round(float(time), 4) for time in beat_times],
        downbeat_times=[round(float(time), 4) for time in downbeat_times],
        transient_times=[float(time) for time in detect_transients(signal, spectrogram)],
    )
