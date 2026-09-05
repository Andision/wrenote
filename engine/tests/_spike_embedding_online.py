"""Spike: embedding-only online clustering — does it work on real audio?

Goal: validate that the approach we'd actually use in production
(pyannote/embedding per VAD segment + online cosine clustering) gives
results close to the full pyannote pipeline (which is our ground truth).

Outputs:
  * Within-speaker vs cross-speaker cosine distance distribution
    (we want clear separation between these two distributions)
  * Optimal threshold for separation
  * Online clustering accuracy vs pipeline labels
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import adjusted_rand_score, confusion_matrix

MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.mp3")
DURATION_S = 120.0
SR = 16_000


def decode(mp3: Path, duration_s: float) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(mp3),
        "-t", f"{duration_s:.3f}", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def run_full_pipeline(wav_path: str, token: str) -> list[tuple[float, float, str]]:
    """Use full pyannote pipeline as 'ground truth' for speaker labels."""
    from pyannote.audio import Pipeline

    print("Loading full diarization pipeline (for ground truth) ...", flush=True)
    t0 = time.monotonic()
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))
    print(f"  loaded in {time.monotonic()-t0:.1f}s", flush=True)

    t1 = time.monotonic()
    output = pipeline(wav_path)
    annotation = getattr(output, "speaker_diarization", output)
    print(f"  diarized in {time.monotonic()-t1:.1f}s", flush=True)

    return [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def load_embedding_model(token: str):
    """Load SpeechBrain ECAPA-TDNN (non-gated, 192-dim)."""
    from speechbrain.inference.speaker import EncoderClassifier
    print("Loading speechbrain/spkrec-ecapa-voxceleb ...", flush=True)
    t0 = time.monotonic()
    classifier = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="/tmp/spkrec-ecapa-voxceleb",
        run_opts={"device": "cpu"},  # CPU is plenty fast for short clips
    )
    print(f"  loaded in {time.monotonic()-t0:.1f}s", flush=True)
    return classifier


def extract_emb(model, audio: np.ndarray) -> np.ndarray:
    """Extract one 192-dim embedding from float32 mono 16kHz audio."""
    if audio.size < SR // 2:
        return np.zeros(192, dtype=np.float32)
    with torch.no_grad():
        wav = torch.from_numpy(audio).unsqueeze(0)
        emb = model.encode_batch(wav)
    return emb.squeeze().cpu().numpy().astype(np.float32)


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a) + 1e-9
    nb = np.linalg.norm(b) + 1e-9
    return 1.0 - float(np.dot(a, b) / (na * nb))


def online_cluster(
    embeddings: list[np.ndarray],
    threshold: float,
) -> list[int]:
    """Simple online clustering: for each new emb, compare to existing
    centroids; assign to closest if within threshold, else new cluster."""
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    labels: list[int] = []
    for emb in embeddings:
        if np.allclose(emb, 0):
            labels.append(-1)  # unknown (too short)
            continue
        if not centroids:
            centroids.append(emb.copy())
            counts.append(1)
            labels.append(0)
            continue
        # find closest centroid
        dists = [cosine_distance(emb, c) for c in centroids]
        best = int(np.argmin(dists))
        if dists[best] < threshold:
            # update centroid (running mean)
            counts[best] += 1
            centroids[best] = centroids[best] + (emb - centroids[best]) / counts[best]
            labels.append(best)
        else:
            centroids.append(emb.copy())
            counts.append(1)
            labels.append(len(centroids) - 1)
    return labels


def main() -> None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("Need HF_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    wav_path = "/tmp/spike_emb_input.wav"
    print(f"Decoding {DURATION_S}s of {MP3.name} → {wav_path}", flush=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(MP3),
        "-t", f"{DURATION_S:.3f}", "-ar", str(SR), "-ac", "1", wav_path,
    ], check=True)

    audio = decode(MP3, DURATION_S)

    print("\n========== Step 1: get ground-truth segments from full pipeline ==========")
    turns = run_full_pipeline(wav_path, token)
    print(f"  {len(turns)} turns, {len({s for _,_,s in turns})} speakers")
    truth_labels_str = [t[2] for t in turns]
    speakers_unique = sorted(set(truth_labels_str))
    spk_to_idx = {s: i for i, s in enumerate(speakers_unique)}
    truth_labels = np.array([spk_to_idx[s] for s in truth_labels_str])
    print(f"  speaker labels: {speakers_unique}")

    # Filter to turns >= 2s to simulate what we'd actually embed in production
    # (Silero VAD segments tend to be 5-20s; the very short turns here are
    # cross-talk that pyannote split but our pipeline would merge into a
    # single VAD segment).
    MIN_TURN_S = float(os.environ.get("MIN_TURN_S", "2.0"))
    print(f"\n  Filtering turns >= {MIN_TURN_S}s (simulates production VAD segments)")
    long_turns = [(s, e, spk) for s, e, spk in turns if (e - s) >= MIN_TURN_S]
    print(f"  kept {len(long_turns)} / {len(turns)} turns "
          f"(total speech: {sum(e-s for s,e,_ in long_turns):.1f}s)")
    turns = long_turns
    truth_labels_str = [t[2] for t in turns]
    truth_labels = np.array([spk_to_idx[s] for s in truth_labels_str])

    print("\n========== Step 2: extract embedding per turn ==========")
    model = load_embedding_model(token)
    embs = []
    t0 = time.monotonic()
    for start, end, spk in turns:
        clip = audio[int(start * SR) : int(end * SR)]
        embs.append(extract_emb(model, clip))
    dt = time.monotonic() - t0
    print(f"  extracted {len(embs)} embeddings in {dt:.1f}s ({dt/len(embs)*1000:.0f}ms/seg)", flush=True)
    print(f"  emb dim: {embs[0].shape}")

    embs_arr = np.array(embs)

    # === Step 3: within-vs-cross speaker distance distribution ===
    print("\n========== Step 3: within-vs-cross speaker distances ==========")
    within = []
    cross = []
    n = len(embs)
    for i in range(n):
        if np.allclose(embs[i], 0):
            continue
        for j in range(i + 1, n):
            if np.allclose(embs[j], 0):
                continue
            d = cosine_distance(embs[i], embs[j])
            if truth_labels_str[i] == truth_labels_str[j]:
                within.append(d)
            else:
                cross.append(d)
    within = np.array(within)
    cross = np.array(cross)
    print(f"  within-speaker distances (n={len(within)}):  "
          f"min={within.min():.3f}  median={np.median(within):.3f}  "
          f"mean={within.mean():.3f}  max={within.max():.3f}")
    print(f"  cross-speaker  distances (n={len(cross)}):  "
          f"min={cross.min():.3f}  median={np.median(cross):.3f}  "
          f"mean={cross.mean():.3f}  max={cross.max():.3f}")
    sep = cross.mean() - within.mean()
    print(f"  separation (cross.mean − within.mean): {sep:.3f}")
    if sep < 0.1:
        print("  ⚠️  poor separation — embeddings may not distinguish these speakers cleanly")
    elif sep < 0.2:
        print("  ⚠️  moderate separation — threshold tuning is sensitive")
    else:
        print("  ✓ good separation")

    # === Step 4: scan thresholds, see online clustering accuracy ===
    print("\n========== Step 4: online clustering across thresholds ==========")
    print(f"  {'threshold':>9}  {'#clusters':>9}  ARI    detail")
    for thresh in [0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        pred = online_cluster(embs, thresh)
        n_clusters = len(set(p for p in pred if p >= 0))
        # ARI requires no -1; replace with placeholder cluster
        pred_for_ari = [p if p >= 0 else n_clusters for p in pred]
        ari = adjusted_rand_score(truth_labels, pred_for_ari)
        # confusion
        cm = confusion_matrix(truth_labels, pred_for_ari)
        cm_str = " ".join(str(c) for c in cm.flatten()[:6])
        print(f"  {thresh:>9.2f}  {n_clusters:>9}  {ari:5.3f}  cm={cm_str}")

    print("\nLegend: ARI=1.0 means online clustering perfectly matches batch pipeline")
    print("        ARI=0.0 means random; ARI>0.7 is usually fine in practice")


if __name__ == "__main__":
    main()
