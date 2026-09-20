"""Export the beat_this "final0" checkpoint to ONNX so the app can run it without PyTorch.

Dev-only: needs the dev dependency group (torch, beat-this, onnx). Run with
    uv run python scripts/export_beat_model.py
and the model lands in woodshed/models/, where the app and the bundle build expect it.
"""
from pathlib import Path

import torch
from beat_this.inference import load_model

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "woodshed" / "models" / "beat_this_final0.onnx"
CHUNK_FRAMES = 1500  # the chunk length beat_this was trained with and uses at inference


class LogitsOnly(torch.nn.Module):
    """The checkpoint returns a dict; ONNX wants positional outputs."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, spectrogram: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        prediction = self.model(spectrogram)
        return prediction["beat"], prediction["downbeat"]


def main() -> None:
    model = LogitsOnly(load_model("final0", "cpu")).eval()
    example = torch.zeros(1, CHUNK_FRAMES, 128)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, (example,), str(OUTPUT_PATH), input_names=["spectrogram"], output_names=["beat", "downbeat"],
        dynamo=False, opset_version=17,
    )
    print(f"wrote {OUTPUT_PATH} ({OUTPUT_PATH.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
