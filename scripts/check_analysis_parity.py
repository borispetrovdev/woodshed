"""Compare woodshed's NumPy/ONNX analysis with the reference beat_this (PyTorch) pipeline.

Dev-only (needs the dev dependency group). Usage:
    uv run python scripts/check_analysis_parity.py ~/Music/Woodshed/*.m4a
Exits non-zero if any file's beats drift from the reference.
"""
import sys
import time
from pathlib import Path

import numpy as np
import torch
from beat_this.inference import Audio2Beats
from beat_this.preprocessing import LogMelSpect

from woodshed import analysis

TOLERANCE_SECONDS = 0.021  # one model frame


def fraction_matched(ours: np.ndarray, reference: np.ndarray) -> float:
    if len(reference) == 0:
        return 1.0 if len(ours) == 0 else 0.0
    return float(np.mean([np.min(np.abs(ours - time)) <= TOLERANCE_SECONDS for time in reference])) if len(ours) else 0.0


def main() -> int:
    reference_tracker = Audio2Beats(checkpoint_path="final0", device="cpu", dbn=False)
    failures = 0
    for path in map(Path, sys.argv[1:]):
        signal = analysis.decode_to_mono(path)
        reference_spectrogram = LogMelSpect()(torch.from_numpy(signal)).numpy()
        our_spectrogram = analysis.log_mel_spectrogram(signal)
        spectrogram_error = float(np.abs(reference_spectrogram - our_spectrogram).max())

        reference_beats, reference_downbeats = reference_tracker(signal, analysis.SAMPLE_RATE)
        started = time.monotonic()
        our_beats, our_downbeats = analysis.beats_from_logits(*analysis.predict_beat_logits(our_spectrogram))
        elapsed = time.monotonic() - started

        beat_match = min(fraction_matched(our_beats, reference_beats), fraction_matched(reference_beats, our_beats))
        downbeat_match = min(fraction_matched(our_downbeats, reference_downbeats), fraction_matched(reference_downbeats, our_downbeats))
        ok = beat_match == 1.0 and downbeat_match == 1.0
        failures += not ok
        print(f"{'ok ' if ok else 'BAD'} {path.name}: spectrogram max error {spectrogram_error:.2e}, "
              f"beats {len(our_beats)}/{len(reference_beats)} ({beat_match:.1%}), "
              f"downbeats {len(our_downbeats)}/{len(reference_downbeats)} ({downbeat_match:.1%}), {elapsed:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
