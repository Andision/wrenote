"""Spike: run pyannote diarization on the example MP3.

Goal: see how many speakers it detects, what the timeline looks like, and
how long inference takes on M1 Max.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.mp3")
DURATION_S = 90.0  # first 90s — should include the intro monologue + start of the dialogue

# Output a 16kHz mono WAV via ffmpeg (pyannote handles many formats but WAV is safest).
WAV = Path("/tmp/spike_diar_input.wav")


def decode_to_wav(mp3: Path, wav: Path, duration_s: float) -> None:
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(mp3),
        "-t", f"{duration_s:.3f}", "-ar", "16000", "-ac", "1",
        str(wav),
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    print(f"Decoding {DURATION_S}s of {MP3.name} → {WAV}", flush=True)
    decode_to_wav(MP3, WAV, DURATION_S)
    size_mb = WAV.stat().st_size / 1024 / 1024
    print(f"  → {size_mb:.1f} MB WAV", flush=True)

    print("\nImporting pyannote ...", flush=True)
    from pyannote.audio import Pipeline

    # pyannote/speaker-diarization-3.1 requires HF auth; try without first.
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        print(f"Using HF token from env (length={len(token)})", flush=True)
    else:
        print("No HF_TOKEN in env — will likely fail for gated models.", flush=True)

    print("\nLoading pyannote/speaker-diarization-3.1 ...", flush=True)
    t0 = time.monotonic()
    try:
        # pyannote.audio v4: token argument changed; pass via `token=` or
        # rely on env (HF_TOKEN).
        kwargs = {}
        if token:
            kwargs["token"] = token
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            **kwargs,
        )
    except Exception as e:
        print(f"  failed: {type(e).__name__}: {e}", flush=True)
        msg = str(e).lower()
        if "gated" in msg or "access" in msg or "401" in msg or "403" in msg:
            print("\nThe diarization model is gated — to use it:", flush=True)
            print("  1. Go to https://hf.co/pyannote/speaker-diarization-3.1 and accept the license", flush=True)
            print("  2. Go to https://hf.co/pyannote/segmentation-3.0 and accept the license", flush=True)
            print("  3. Create HF token at https://hf.co/settings/tokens", flush=True)
            print("  4. export HF_TOKEN='hf_...'", flush=True)
        sys.exit(1)
    print(f"  loaded in {time.monotonic()-t0:.1f}s", flush=True)

    # Try Metal (MPS) acceleration.
    try:
        import torch
        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
            print("  using MPS (Metal) backend", flush=True)
        else:
            print("  using CPU backend (no MPS)", flush=True)
    except Exception as e:
        print(f"  device selection skipped: {e}", flush=True)

    print("\nRunning diarization ...", flush=True)
    t1 = time.monotonic()
    output = pipeline(str(WAV))
    dt = time.monotonic() - t1
    rtf = dt / DURATION_S
    print(f"  done in {dt:.1f}s for {DURATION_S}s audio (RTF={rtf:.2f}, {1/rtf:.1f}× realtime)", flush=True)

    # pyannote.audio v4 returns DiarizeOutput; the Annotation lives on a
    # `speaker_diarization` attribute. Older versions returned the Annotation directly.
    annotation = getattr(output, "speaker_diarization", output)
    print(f"  output type: {type(output).__name__}; annotation type: {type(annotation).__name__}", flush=True)

    speakers = set()
    timeline = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        speakers.add(speaker)
        timeline.append((turn.start, turn.end, speaker))

    print(f"\nDetected {len(speakers)} speakers: {sorted(speakers)}")
    print(f"Total turns: {len(timeline)}")
    print("\nTimeline (first 30):")
    for t_start, t_end, speaker in timeline[:30]:
        bar_pos = int(t_start / DURATION_S * 50)
        bar = " " * bar_pos + speaker[-1]  # show position roughly
        print(f"  {t_start:6.2f} - {t_end:6.2f}  {speaker:12}  {bar}")

    # Per-speaker total speaking time
    per_speaker_time: dict[str, float] = {}
    for t_start, t_end, speaker in timeline:
        per_speaker_time[speaker] = per_speaker_time.get(speaker, 0.0) + (t_end - t_start)
    print("\nSpeaking time per speaker:")
    for spk, secs in sorted(per_speaker_time.items(), key=lambda x: -x[1]):
        pct = secs / DURATION_S * 100
        print(f"  {spk}: {secs:5.2f}s  ({pct:.1f}%)")


if __name__ == "__main__":
    main()
