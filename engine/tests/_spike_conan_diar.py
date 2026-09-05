"""Run pyannote full pipeline on Conan show clip — see what's achievable
when we don't constrain ourselves to one-speaker-per-VAD-segment.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import torch

MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_2.mp3")
WAV = Path("/tmp/conan_clip.wav")
DURATION_S = 90.0


def decode(mp3: Path, wav: Path, duration_s: float) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(mp3),
        "-t", f"{duration_s:.3f}", "-ar", "16000", "-ac", "1", str(wav),
    ], check=True)


def main() -> None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("Need HF_TOKEN", file=sys.stderr)
        sys.exit(1)

    print(f"Decoding {DURATION_S}s of {MP3.name} → {WAV}", flush=True)
    decode(MP3, WAV, DURATION_S)

    from pyannote.audio import Pipeline

    print("Loading pyannote/speaker-diarization-3.1 ...", flush=True)
    t0 = time.monotonic()
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    print(f"  loaded in {time.monotonic()-t0:.1f}s", flush=True)

    print("\nRunning diarization ...", flush=True)
    t1 = time.monotonic()
    output = pipeline(str(WAV))
    annotation = getattr(output, "speaker_diarization", output)
    dt = time.monotonic() - t1
    print(f"  done in {dt:.1f}s ({DURATION_S/dt:.1f}× realtime)", flush=True)

    speakers = set()
    timeline = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        speakers.add(speaker)
        timeline.append((turn.start, turn.end, speaker))

    print(f"\nDetected {len(speakers)} speakers; {len(timeline)} turns")
    print("\nTimeline (all turns):")
    print(f"  {'start':>6}  {'end':>6}  {'dur':>5}  speaker")
    per_speaker = {}
    for t_start, t_end, spk in timeline:
        dur = t_end - t_start
        per_speaker[spk] = per_speaker.get(spk, 0.0) + dur
        print(f"  {t_start:6.2f}  {t_end:6.2f}  {dur:5.2f}  {spk}")

    print("\nTotal time per speaker:")
    for spk, secs in sorted(per_speaker.items(), key=lambda x: -x[1]):
        print(f"  {spk}: {secs:5.1f}s ({secs/DURATION_S*100:.1f}%)")


if __name__ == "__main__":
    main()
