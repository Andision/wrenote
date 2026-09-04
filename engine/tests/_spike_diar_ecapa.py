"""Spike: speaker diarization via SpeechBrain ECAPA-TDNN embeddings.

Pipeline:
  1. Decode MP3 → 16kHz mono PCM
  2. Silero VAD: get speech segments
  3. For each segment, extract ECAPA-TDNN embedding (192-dim)
  4. Agglomerative clustering with cosine distance threshold
  5. Print timeline + per-speaker stats

This is a quick offline test to see how cleanly the model separates speakers
on our example MP3. No streaming, no online clustering — just batch.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.cluster import AgglomerativeClustering

MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.mp3")
DURATION_S = 120.0
SR = 16_000


def decode_to_float32(mp3: Path, duration_s: float) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(mp3),
        "-t", f"{duration_s:.3f}", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-",
    ]
    pcm_bytes = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def silero_segments(audio: np.ndarray, min_speech_s: float = 0.5) -> list[tuple[float, float]]:
    """Use Silero VAD to get speech segments. Returns [(start_s, end_s), ...]."""
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad(onnx=False)
    ts = get_speech_timestamps(
        torch.from_numpy(audio),
        model,
        sampling_rate=SR,
        min_speech_duration_ms=int(min_speech_s * 1000),
        min_silence_duration_ms=400,
    )
    return [(t["start"] / SR, t["end"] / SR) for t in ts]


def extract_embeddings(audio: np.ndarray, segments: list[tuple[float, float]]) -> np.ndarray:
    """Extract one embedding per segment via ECAPA-TDNN. Returns shape (N, 192)."""
    from speechbrain.inference.speaker import EncoderClassifier

    print("Loading ECAPA-TDNN ...", flush=True)
    t0 = time.monotonic()
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="/tmp/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"},
    )
    print(f"  loaded in {time.monotonic()-t0:.1f}s", flush=True)

    embs = []
    t1 = time.monotonic()
    for i, (start, end) in enumerate(segments):
        seg = audio[int(start * SR) : int(end * SR)]
        if seg.size < SR // 2:
            # < 0.5s, ECAPA is unreliable on very short clips
            embs.append(np.zeros(192, dtype=np.float32))
            continue
        with torch.no_grad():
            emb = classifier.encode_batch(torch.from_numpy(seg).unsqueeze(0))
        embs.append(emb.squeeze().cpu().numpy())
    dt = time.monotonic() - t1
    print(f"  extracted {len(embs)} embeddings in {dt:.1f}s ({dt/len(embs)*1000:.0f}ms/segment)", flush=True)
    return np.array(embs)


def cluster(embs: np.ndarray, threshold: float = 0.55) -> np.ndarray:
    """Agglomerative clustering with cosine distance threshold."""
    # Normalize and use 1 - cosine_similarity as distance
    norms = np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    normed = embs / norms
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="cosine",
        linkage="average",
    )
    return clusterer.fit_predict(normed)


def main() -> None:
    print(f"Decoding {DURATION_S}s of {MP3.name} ...", flush=True)
    audio = decode_to_float32(MP3, DURATION_S)
    print(f"  {audio.size} samples ({audio.size / SR:.1f}s)", flush=True)

    print("\nRunning Silero VAD ...", flush=True)
    t0 = time.monotonic()
    segments = silero_segments(audio)
    print(f"  {len(segments)} speech segments in {time.monotonic()-t0:.1f}s", flush=True)

    print("\nExtracting embeddings ...", flush=True)
    embs = extract_embeddings(audio, segments)

    for threshold in (0.40, 0.50, 0.55, 0.60, 0.70):
        labels = cluster(embs, threshold=threshold)
        n_speakers = len(set(labels))
        speaker_secs: dict[int, float] = {}
        for (start, end), lbl in zip(segments, labels):
            speaker_secs[int(lbl)] = speaker_secs.get(int(lbl), 0.0) + (end - start)
        print(f"\nthreshold={threshold:.2f}  →  {n_speakers} speakers")
        for s, secs in sorted(speaker_secs.items(), key=lambda x: -x[1])[:10]:
            print(f"   speaker {s}: {secs:5.1f}s ({secs / DURATION_S * 100:4.1f}%)")

    # Show timeline at threshold=0.55 (typical default)
    labels = cluster(embs, threshold=0.55)
    print("\nTimeline (threshold=0.55, first 40 segments):")
    print(f"  {'start':>6} - {'end':>6}  spk  text")
    for i, ((start, end), lbl) in enumerate(zip(segments, labels)):
        if i >= 40:
            break
        bar = "  " + "·" * int(start / DURATION_S * 60) + str(lbl)
        print(f"  {start:6.2f} - {end:6.2f}    {lbl}{bar[:90]}")


if __name__ == "__main__":
    main()
