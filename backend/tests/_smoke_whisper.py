"""Direct smoke test for the whisper.cpp backend.

Decodes an MP3 to 16k mono int16 PCM via ffmpeg, picks a 30-second window,
and runs transcribe_segment. Bypasses WS / Pipeline; isolates whether the
model + binding work on this Mac.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import interpreter  # noqa: F401 -- registers backends
from interpreter.core.events import AudioSegment
from interpreter.core.registry import make_stt

DEFAULT_MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.mp3")
DEFAULT_MODEL = Path("~/.interpreter/models/ggml-large-v3-turbo-q5_0.bin").expanduser()


def decode_mp3_to_pcm(mp3_path: Path, *, sample_rate: int = 16000) -> bytes:
    """Use ffmpeg to decode any input to mono int16 PCM at the target rate."""
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(mp3_path),
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout


async def main() -> None:
    mp3 = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MP3
    model_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MODEL
    duration_s = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

    print(f"Decoding {mp3} → 16kHz mono int16 PCM …")
    pcm = decode_mp3_to_pcm(mp3)
    total_s = len(pcm) / 2 / 16000
    print(f"  decoded: {len(pcm)} bytes ({total_s:.1f}s)")

    n_bytes = int(duration_s * 16000 * 2)
    window = pcm[:n_bytes]
    print(f"  using first {len(window)/2/16000:.1f}s for transcription")

    stt = make_stt("whisper_cpp", {"model_path": str(model_path), "language": "en"})
    print(f"\nLoading model: {model_path.name} …")
    t0 = time.monotonic()
    await stt.load()
    print(f"  loaded in {time.monotonic()-t0:.1f}s")

    seg = AudioSegment(segment_id="test", pcm=window, t0=0.0, t1=len(window)/2/16000)
    print("\nTranscribing …")
    t1 = time.monotonic()
    event = await stt.transcribe_segment(seg, src_lang="en")
    dt = time.monotonic() - t1
    audio_s = len(window) / 2 / 16000
    rtf = dt / audio_s if audio_s > 0 else 0
    print(f"  inference: {dt:.2f}s for {audio_s:.1f}s audio (RTF={rtf:.2f}, {1/rtf:.1f}× realtime)\n")
    print(f"--- Transcript ({len(event.text)} chars) ---")
    print(event.text)
    print("--- end ---")

    await stt.unload()


if __name__ == "__main__":
    asyncio.run(main())
