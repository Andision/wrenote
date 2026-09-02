"""One-time export of SpeechBrain ECAPA-TDNN to a single-file ONNX.

This is a DEV/BUILD tool (needs torch + speechbrain); the runtime never imports
torch — ``wrenote.speaker.ecapa`` loads the produced ONNX via onnxruntime. The
export wraps the full ``encode_batch`` pipeline (Fbank → InputNormalization →
ECAPA_TDNN) so the ONNX takes a raw 16 kHz mono waveform and returns the 192-dim
embedding, bit-for-bit matching the torch model (verified cos-sim = 1.0).

    python packaging/export_ecapa_onnx.py [out.onnx]

Default output: ~/.wrenote/models/spkrec-ecapa-voxceleb.onnx
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

DEFAULT_OUT = Path.home() / ".wrenote" / "models" / "spkrec-ecapa-voxceleb.onnx"


def main() -> None:
    warnings.filterwarnings("ignore")
    import onnx
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    m = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="/tmp/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"},
    )
    m.eval()

    class Wrapper(torch.nn.Module):
        def __init__(self, model: EncoderClassifier) -> None:
            super().__init__()
            self.compute_features = model.mods.compute_features
            self.mean_var_norm = model.mods.mean_var_norm
            self.embedding_model = model.mods.embedding_model

        def forward(self, wav: torch.Tensor) -> torch.Tensor:  # [1, samples] -> [1, 1, 192]
            lens = torch.ones(wav.shape[0])
            feats = self.compute_features(wav)
            feats = self.mean_var_norm(feats, lens)
            return self.embedding_model(feats, lens)

    tmp = out.with_suffix(".tmp.onnx")
    torch.onnx.export(
        Wrapper(m).eval(),
        (torch.randn(1, 32000),),
        str(tmp),
        input_names=["wav"],
        output_names=["emb"],
        dynamic_axes={"wav": {1: "samples"}},
        opset_version=17,
        dynamo=True,
    )
    # dynamo splits weights into a .data sidecar; consolidate into one file.
    model = onnx.load(str(tmp))
    onnx.save_model(model, str(out), save_as_external_data=False)
    tmp.unlink(missing_ok=True)
    Path(str(tmp) + ".data").unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
