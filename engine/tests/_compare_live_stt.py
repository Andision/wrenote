"""Compare the live recognisers on your own recordings.

    python tests/_compare_live_stt.py DIR [--models zipformer,paraformer,whisper]

DIR holds 16 kHz mono WAVs. A ``<name>.txt`` next to a WAV is its reference
transcript and turns on the character error rate. Each model is fed the
audio at 100 ms chunks the way a live session would, so the numbers are
the live path's: wall time, first-text latency, how many times the
on-screen text changed, and how many of those changes rewrote what was
already shown (a flicker). Whisper runs its VAD-free growing-buffer
partials for the same comparison; it needs pywhispercpp and the model.

Model files come from ~/.wrenote/models by default (the catalogue's names)
or from --models-dir. This is a hand tool, not a test: nothing here is
asserted, only printed.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import re
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wrenote.core.events import AudioSegment, TranscriptEvent  # noqa: E402
from wrenote.core.pipeline import Pipeline, SessionParams  # noqa: E402
from wrenote.translator.mock import MockTranslatorBackend  # noqa: E402
from wrenote.vad.disabled import DisabledVAD  # noqa: E402

CHUNK_BYTES = 3200  # 100 ms of 16 kHz int16


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\s,.!?;:，。！？；：、\"'“”‘’()（）\-]+", "", text)
    return text


def cer(hyp: str, ref: str) -> float:
    h, r = _norm(hyp), _norm(ref)
    if not r:
        return 0.0
    sm = difflib.SequenceMatcher(a=r, b=h, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    # Insertions count too: distance ≈ |r| - matched + (|h| - matched).
    return max(0.0, (len(r) - matched) + (len(h) - matched)) / len(r)


def _stt(name: str, models_dir: Path):
    if name == "zipformer":
        from wrenote.stt.sherpa_onnx import SherpaOnnxBackend
        return SherpaOnnxBackend(
            encoder_path=str(models_dir / "zipformer-zh-en-encoder.int8.onnx"),
            decoder_path=str(models_dir / "zipformer-zh-en-decoder.onnx"),
            joiner_path=str(models_dir / "zipformer-zh-en-joiner.int8.onnx"),
            tokens_path=str(models_dir / "zipformer-zh-en-tokens.txt"),
        )
    if name == "paraformer":
        from wrenote.stt.sherpa_onnx import SherpaOnnxBackend
        return SherpaOnnxBackend(
            encoder_path=str(models_dir / "paraformer-zh-en-encoder.int8.onnx"),
            decoder_path=str(models_dir / "paraformer-zh-en-decoder.int8.onnx"),
            tokens_path=str(models_dir / "paraformer-zh-en-tokens.txt"),
        )
    if name == "whisper":
        from wrenote.stt.whisper_cpp import WhisperCppBackend
        candidates = sorted(models_dir.glob("ggml-*.bin"))
        if not candidates:
            raise SystemExit(f"no ggml-*.bin in {models_dir}")
        return WhisperCppBackend(model_path=str(candidates[-1]))
    raise SystemExit(f"unknown model {name}")


async def run_one(stt, pcm: bytes, src: str) -> dict:
    p = Pipeline(
        stt=stt, vad=DisabledVAD(), translator=MockTranslatorBackend(delay_s=0),
        params=SessionParams(src_lang=src, tgt_lang="en", translate_enabled=False,
                             partial_interval_ms=300, min_silence_ms=800),
    )
    await p.start()
    events: list = []

    async def pump() -> None:
        async for ev in p.client_event_stream():
            events.append((time.perf_counter(), ev))

    task = asyncio.create_task(pump())
    t0 = time.perf_counter()
    for i in range(0, len(pcm), CHUNK_BYTES):
        await p.feed_audio(pcm[i : i + CHUNK_BYTES])
        # Real time: the models are measured against the clock the user lives on.
        await asyncio.sleep(max(0.0, t0 + (i // CHUNK_BYTES + 1) * 0.1 - time.perf_counter()))
    await p.flush()
    await asyncio.sleep(0.2)
    task.cancel()
    await p.stop()

    finals = [ev for _, ev in events if isinstance(ev, TranscriptEvent) and ev.type == "final"]
    partials = [(t, ev) for t, ev in events if isinstance(ev, TranscriptEvent) and ev.type == "partial"]
    first_text = next((t for t, ev in partials if ev.text.strip()), None)
    shown: dict[str, str] = {}
    changes = rewrites = 0
    for _, ev in partials:
        prev = shown.get(ev.segment_id, "")
        if ev.text != prev:
            changes += 1
            if not ev.text.startswith(prev):
                rewrites += 1
            shown[ev.segment_id] = ev.text
    return {
        "text": " ".join(f.text for f in finals),
        "segments": len(finals),
        "first_text_s": (first_text - t0) if first_text else None,
        "changes": changes,
        "rewrites": rewrites,
        "wall_s": time.perf_counter() - t0,
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--models", default="zipformer,paraformer")
    ap.add_argument("--models-dir", type=Path, default=Path("~/.wrenote/models").expanduser())
    ap.add_argument("--src", default="zh", help="main language the recordings are in")
    args = ap.parse_args()
    wavs = sorted(args.dir.glob("*.wav"))
    if not wavs:
        raise SystemExit(f"no WAVs in {args.dir}")
    for name in args.models.split(","):
        stt = _stt(name.strip(), args.models_dir)
        print(f"\n=== {name} ===")
        total_cer: list[float] = []
        for wav in wavs:
            with wave.open(str(wav)) as wf:
                assert wf.getframerate() == 16000 and wf.getnchannels() == 1, wav
                pcm = wf.readframes(wf.getnframes())
            r = await run_one(stt, pcm, args.src)
            ref = wav.with_suffix(".txt")
            line = (
                f"{wav.name:16s} {len(pcm) / 32000:5.1f}s  wall {r['wall_s']:5.1f}s  "
                f"first {r['first_text_s'] or 0:4.1f}s  segs {r['segments']:2d}  "
                f"changes {r['changes']:3d}  rewrites {r['rewrites']:2d}"
            )
            if ref.exists():
                c = cer(r["text"], ref.read_text(encoding="utf-8"))
                total_cer.append(c)
                line += f"  CER {c * 100:5.1f}%"
            print(line)
            print(f"    {r['text']!r}")
        if total_cer:
            print(f"mean CER {sum(total_cer) / len(total_cer) * 100:.1f}% over {len(total_cer)} clip(s)")


if __name__ == "__main__":
    asyncio.run(main())
