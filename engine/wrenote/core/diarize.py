"""Offline speaker diarization for a finished session.

Design: use the finished recording as a whole-session diarization pass, then
project the speaker timeline back into transcript rows. Upload/STT segments are
good text anchors, but one row can still contain several turns; the post-pass is
allowed to rewrite rows when there is enough speaker evidence or explicit
subtitle-style turn markers in the text.

Algorithm:
  1. Trim obvious head/tail silence inside each transcript segment. Live VAD
     segments intentionally include the silence that closed the segment; on
     short turns that silence can otherwise dominate speaker embeddings. The
     trim is conservative for merged upload rows so a quiet speaker before a
     louder speaker is not clipped away.
  2. Slice the **whole session timeline** into overlapping speech windows,
     embed every usable window, and cluster all windows together. This uses
     the full recording context instead of forcing one average embedding per
     transcript segment.
  3. Treat the clustered windows as a coarse speaker timeline, then rewrite
     transcript rows around explicit dialogue markers or stable speaker runs.
     Low-coverage or mixed intervals become "unknown" rather than being
     confidently mislabeled.
"""
from __future__ import annotations

import logging
import re
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

from ..speaker.base import SpeakerBackend

log = logging.getLogger(__name__)

# Segments shorter than this don't yield reliable embeddings; they get
# the "unknown" label rather than being clustered.
MIN_SEGMENT_SECONDS = 1.0

# Sliding-window length for whole-session embedding. ECAPA is reliable at ≥1s.
# Hop < window for 50% overlap → enough temporal resolution for short turns.
SUB_WINDOW_S = 1.0
SUB_HOP_S = 0.5

# Auto-K search range. >5 speakers in one recording is rare for meeting
# / call audio. Per-segment N is much smaller than per-window N, so we
# also tier the upper bound by N (see _pick_k).
MAX_K = 5

# Energy trim settings for removing the VAD hangover silence from segment
# boundaries. Values are deliberately conservative: if background noise makes
# speech/silence hard to separate, we prefer leaving audio in place over
# clipping a quiet speaker.
TRIM_FRAME_S = 0.03
TRIM_HOP_S = 0.01
TRIM_PAD_S = 0.12
TRIM_ABS_RMS = 0.003
TRIM_REL_RMS = 0.08

# Silhouette acceptance floors. Small-N clustering is volatile; allow K=2 for
# short conversations only when the separation is very clear.
DEFAULT_MIN_SILHOUETTE = 0.08
SMALL_N_MIN_SILHOUETTE = 0.18
MIN_CLUSTER_COSINE_DISTANCE = 0.12

# Segment assignment from the clustered speaker timeline. Coverage is computed
# on a small frame grid so overlapping embedding windows don't overweight the
# middle of a long segment too heavily.
ASSIGN_FRAME_S = 0.05
MIN_SEGMENT_COVERAGE = 0.20
MIN_SEGMENT_DOMINANCE = 0.55
MIN_RESEGMENT_RUN_S = 0.40
_DIALOGUE_TURN_RE = re.compile(r"\s+-(?!-)")
_RESEGMENTED_ID_RE = re.compile(r"^(?P<base>.+)-r\d{2,}$")
_TURN_END_CHARS = set('.?!"\u3002\uff01\uff1f')


@dataclass
class DiarizeResult:
    """Outcome of one diarize pass. Caller may replace transcript rows."""
    labels: dict[str, str]
    """{segment_id: speaker} for the final segment ids."""
    segments: list[dict[str, Any]]
    """Speaker-aware transcript rows. Caller replaces existing DB rows with this."""


@dataclass
class _SpeechSegment:
    segment_id: str
    orig_t0: float
    orig_t1: float
    speech_t0: float
    speech_t1: float

    @property
    def speech_duration(self) -> float:
        return max(0.0, self.speech_t1 - self.speech_t0)


@dataclass
class _EmbeddingWindow:
    t0: float
    t1: float
    embedding: np.ndarray


@dataclass
class _LabeledWindow:
    t0: float
    t1: float
    label: int


def _read_wav_int16_mono(path: Path) -> tuple[np.ndarray, int]:
    """Return (samples, sample_rate). Raises on bad / multi-channel WAV."""
    with wave.open(str(path), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if nch != 1 or sw != 2:
        raise ValueError(f"Expected mono int16 WAV, got {nch}ch {sw*8}bit")
    return np.frombuffer(frames, dtype=np.int16), sr


def _slice_pcm_bytes(
    samples: np.ndarray, sr: int, t0: float, t1: float
) -> bytes:
    """Return little-endian int16 PCM bytes for [t0, t1)."""
    i0 = max(0, int(t0 * sr))
    i1 = min(len(samples), int(t1 * sr))
    if i1 <= i0:
        return b""
    return samples[i0:i1].tobytes()


def _pick_k(embeddings: np.ndarray, max_k: int) -> int:
    """Pick the best K in [2, effective_upper] by silhouette.

    Sample-size guards are critical here: per-segment N can be small
    (10-30 segments for short calls), and silhouette on small N is very
    noisy. We tier the upper bound by N so we don't over-cluster.
    """
    n = len(embeddings)
    if n < 4:
        # Fewer than 4 embeddable turns is not enough evidence for a stable
        # multi-speaker decision.
        return 1
    if n < 10:
        upper = min(max_k, 2, n - 1)
        min_score = SMALL_N_MIN_SILHOUETTE
    elif n < 30:
        upper = min(max_k, 3, n - 1)
        min_score = DEFAULT_MIN_SILHOUETTE
    else:
        upper = min(max_k, n - 1)
        min_score = DEFAULT_MIN_SILHOUETTE
    if upper < 2:
        return 1

    best_k, best_score = 1, -1.0
    for k in range(2, upper + 1):
        cl = AgglomerativeClustering(
            n_clusters=k, metric="cosine", linkage="average"
        )
        labels = cl.fit_predict(embeddings)
        if len(set(labels)) < 2:
            continue
        if _min_cluster_distance(embeddings, labels) < MIN_CLUSTER_COSINE_DISTANCE:
            continue
        try:
            score = silhouette_score(embeddings, labels, metric="cosine")
        except Exception:
            continue
        log.debug("diarize: k=%d silhouette=%.3f (n=%d)", k, score, n)
        if score > best_score:
            best_score, best_k = score, k
    return best_k if best_score >= min_score else 1


def _min_cluster_distance(embeddings: np.ndarray, labels: np.ndarray) -> float:
    """Smallest cosine distance between cluster centroids."""
    centroids: list[np.ndarray] = []
    for label in sorted(set(int(x) for x in labels)):
        members = embeddings[labels == label]
        if len(members) == 0:
            continue
        centroid = members.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm < 1e-9:
            continue
        centroids.append(centroid / norm)

    if len(centroids) < 2:
        return 0.0

    best = float("inf")
    for i, a in enumerate(centroids):
        for b in centroids[i + 1 :]:
            best = min(best, 1.0 - float(np.dot(a, b)))
    return best


def _trim_speech_bounds(
    samples: np.ndarray,
    sr: int,
    t0: float,
    t1: float,
) -> tuple[float, float]:
    """Return tighter [t0, t1) bounds by trimming low-energy edges.

    This is not a VAD replacement; it only removes obvious leading/trailing
    silence from an already-detected speech segment. If the segment is too
    noisy to separate confidently, it returns the original bounds.
    """
    i0 = max(0, int(t0 * sr))
    i1 = min(len(samples), int(t1 * sr))
    if i1 <= i0:
        return t0, t0

    audio = samples[i0:i1].astype(np.float32) / 32768.0
    if audio.size == 0:
        return t0, t0

    frame = max(1, int(TRIM_FRAME_S * sr))
    hop = max(1, int(TRIM_HOP_S * sr))
    if audio.size < frame:
        rms = float(np.sqrt(np.mean(audio * audio)))
        return (t0, t1) if rms >= TRIM_ABS_RMS else (t0, t0)

    starts = list(range(0, audio.size - frame + 1, hop))
    last = audio.size - frame
    if starts[-1] != last:
        starts.append(last)

    rms_values = np.array(
        [
            float(np.sqrt(np.mean(audio[start : start + frame] ** 2)))
            for start in starts
        ],
        dtype=np.float32,
    )
    max_rms = float(rms_values.max(initial=0.0))
    if max_rms < TRIM_ABS_RMS:
        return t0, t0

    floor = float(np.percentile(rms_values, 20))
    threshold_parts = [TRIM_ABS_RMS, max_rms * TRIM_REL_RMS]
    # Only treat the low percentile as a noise floor when it is clearly far
    # below the loudest speech. Merged upload segments may contain a quiet
    # speaker followed by a loud speaker; using floor * 2.5 unconditionally
    # would trim that quiet speaker from the edge.
    if floor < max_rms * 0.20:
        threshold_parts.append(floor * 2.5)
    threshold = max(threshold_parts)
    threshold = min(threshold, max_rms * 0.6)
    voiced = rms_values >= threshold
    if not np.any(voiced):
        # Last-resort fallback for very quiet, low-contrast speech.
        voiced = rms_values >= max(TRIM_ABS_RMS * 0.5, max_rms * 0.05)
    if not np.any(voiced):
        return t0, t0

    idx = np.flatnonzero(voiced)
    pad = int(TRIM_PAD_S * sr)
    trim_i0 = i0 + max(0, starts[int(idx[0])] - pad)
    trim_i1 = i0 + min(audio.size, starts[int(idx[-1])] + frame + pad)
    if trim_i1 <= trim_i0:
        return t0, t0
    return trim_i0 / sr, trim_i1 / sr


def _renumber_by_first_occurrence(labels: Iterable[int]) -> list[int]:
    """Relabel cluster ids so the first-appearing cluster becomes 0, then 1, …
    Stable across runs given the same input order."""
    remap: dict[int, int] = {}
    out: list[int] = []
    for x in labels:
        if x not in remap:
            remap[x] = len(remap)
        out.append(remap[x])
    return out


def _collect_speech_segments(
    samples: np.ndarray,
    sr: int,
    segments: list[dict],
) -> tuple[list[_SpeechSegment], dict[str, str]]:
    speech_segments: list[_SpeechSegment] = []
    out_labels: dict[str, str] = {}
    for seg in segments:
        sid = seg["segment_id"]
        seg_t0 = float(seg.get("started_at") or 0.0)
        seg_t1 = float(seg.get("ended_at") or 0.0)
        speech_t0, speech_t1 = _trim_speech_bounds(samples, sr, seg_t0, seg_t1)
        speech = _SpeechSegment(
            segment_id=sid,
            orig_t0=seg_t0,
            orig_t1=seg_t1,
            speech_t0=speech_t0,
            speech_t1=speech_t1,
        )
        speech_segments.append(speech)
        if speech.speech_duration <= 0:
            out_labels[sid] = "unknown"
    return speech_segments, out_labels


async def _embed_session_windows(
    *,
    samples: np.ndarray,
    sr: int,
    speech_segments: list[_SpeechSegment],
    speaker: SpeakerBackend,
    on_progress: Callable[[float, str | None], None] | None,
) -> list[_EmbeddingWindow]:
    jobs: list[tuple[float, float]] = []
    for seg in speech_segments:
        if seg.speech_duration < MIN_SEGMENT_SECONDS:
            continue
        for win_t0 in _window_starts(seg.speech_duration, SUB_WINDOW_S, SUB_HOP_S):
            abs_t0 = seg.speech_t0 + win_t0
            abs_t1 = min(seg.speech_t1, abs_t0 + SUB_WINDOW_S)
            if abs_t1 - abs_t0 >= MIN_SEGMENT_SECONDS:
                jobs.append((abs_t0, abs_t1))

    total = max(1, len(jobs))
    windows: list[_EmbeddingWindow] = []
    for i, (abs_t0, abs_t1) in enumerate(jobs):
        pcm = _slice_pcm_bytes(samples, sr, abs_t0, abs_t1)
        if len(pcm) < sr * 2:
            continue
        try:
            emb = await speaker.embed(pcm, sample_rate=sr)
        except Exception:
            log.exception("embed failed for window %.2f-%.2f", abs_t0, abs_t1)
            continue
        if emb is None or emb.size == 0 or not np.isfinite(emb).all():
            continue
        windows.append(
            _EmbeddingWindow(t0=abs_t0, t1=abs_t1, embedding=emb.astype(np.float32))
        )
        if on_progress is not None:
            on_progress(
                (i + 1) / total,
                f"Embedded {i + 1}/{total}" if (i + 1) % 25 == 0 else None,
            )
    return windows


def _cluster_windows(windows: list[_EmbeddingWindow]) -> tuple[int, list[_LabeledWindow]]:
    if not windows:
        return 0, []

    embs = np.stack([w.embedding for w in windows], axis=0)
    norms = np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    embs = embs / norms

    k = _pick_k(embs, MAX_K)
    if k <= 1:
        return 1, [_LabeledWindow(t0=w.t0, t1=w.t1, label=0) for w in windows]

    cl = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average")
    raw_labels = cl.fit_predict(embs)
    ordered = _renumber_by_first_occurrence(raw_labels)
    labeled = [
        _LabeledWindow(t0=w.t0, t1=w.t1, label=lab)
        for w, lab in zip(windows, ordered, strict=True)
    ]
    return k, labeled


def _assign_segments_from_timeline(
    speech_segments: list[_SpeechSegment],
    labeled_windows: list[_LabeledWindow],
    *,
    single_speaker: bool,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if not labeled_windows and not single_speaker:
        return {seg.segment_id: "unknown" for seg in speech_segments}

    for seg in speech_segments:
        if seg.speech_duration <= 0:
            labels[seg.segment_id] = "unknown"
            continue
        if single_speaker:
            labels[seg.segment_id] = "Speaker 1"
            continue

        counts: dict[int, int] = {}
        covered = 0
        total = max(1, int(np.ceil(seg.speech_duration / ASSIGN_FRAME_S)))
        for frame_i in range(total):
            t = seg.speech_t0 + min(
                seg.speech_duration,
                (frame_i + 0.5) * ASSIGN_FRAME_S,
            )
            active = [w.label for w in labeled_windows if w.t0 <= t < w.t1]
            if not active:
                continue
            covered += 1
            frame_counts: dict[int, int] = {}
            for label in active:
                frame_counts[label] = frame_counts.get(label, 0) + 1
            best = max(frame_counts.items(), key=lambda x: x[1])[0]
            counts[best] = counts.get(best, 0) + 1

        if not counts or covered / total < MIN_SEGMENT_COVERAGE:
            labels[seg.segment_id] = "unknown"
            continue

        best_label, best_count = max(counts.items(), key=lambda x: x[1])
        if best_count / covered < MIN_SEGMENT_DOMINANCE:
            labels[seg.segment_id] = "unknown"
        else:
            labels[seg.segment_id] = f"Speaker {best_label + 1}"

    return labels


def _label_for_interval(
    t0: float,
    t1: float,
    labeled_windows: list[_LabeledWindow],
    *,
    single_speaker: bool,
) -> str:
    if t1 <= t0:
        return "unknown"
    if single_speaker:
        return "Speaker 1"

    counts: dict[int, int] = {}
    covered = 0
    total = max(1, int(np.ceil((t1 - t0) / ASSIGN_FRAME_S)))
    for frame_i in range(total):
        t = t0 + min(t1 - t0, (frame_i + 0.5) * ASSIGN_FRAME_S)
        active = [w.label for w in labeled_windows if w.t0 <= t < w.t1]
        if not active:
            continue
        covered += 1
        frame_counts: dict[int, int] = {}
        for label in active:
            frame_counts[label] = frame_counts.get(label, 0) + 1
        best = max(frame_counts.items(), key=lambda x: x[1])[0]
        counts[best] = counts.get(best, 0) + 1

    if not counts or covered / total < MIN_SEGMENT_COVERAGE:
        return "unknown"
    best_label, best_count = max(counts.items(), key=lambda x: x[1])
    if best_count / covered < MIN_SEGMENT_DOMINANCE:
        return "unknown"
    return f"Speaker {best_label + 1}"


def _speaker_runs_for_interval(
    t0: float,
    t1: float,
    labeled_windows: list[_LabeledWindow],
    *,
    single_speaker: bool,
) -> list[tuple[float, float, str]]:
    label = _label_for_interval(t0, t1, labeled_windows, single_speaker=single_speaker)
    if single_speaker or label == "unknown" or t1 <= t0:
        return [(t0, t1, label)]

    frames: list[tuple[float, str]] = []
    total = max(1, int(np.ceil((t1 - t0) / ASSIGN_FRAME_S)))
    for frame_i in range(total):
        t = t0 + min(t1 - t0, (frame_i + 0.5) * ASSIGN_FRAME_S)
        frame_label = _label_for_interval(
            max(t0, t - ASSIGN_FRAME_S / 2),
            min(t1, t + ASSIGN_FRAME_S / 2),
            labeled_windows,
            single_speaker=False,
        )
        frames.append((t, frame_label))

    runs: list[tuple[float, float, str]] = []
    cur_label = frames[0][1]
    cur_start = t0
    for i, (_, frame_label) in enumerate(frames[1:], start=1):
        if frame_label == cur_label:
            continue
        boundary = t0 + i * ASSIGN_FRAME_S
        runs.append((cur_start, min(t1, boundary), cur_label))
        cur_start = min(t1, boundary)
        cur_label = frame_label
    runs.append((cur_start, t1, cur_label))
    return _merge_short_runs(runs)


def _merge_short_runs(
    runs: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    if len(runs) <= 1:
        return runs
    out: list[tuple[float, float, str]] = []
    for run in runs:
        start, end, label = run
        if out and (end - start) < MIN_RESEGMENT_RUN_S:
            prev_start, _prev_end, prev_label = out[-1]
            out[-1] = (prev_start, end, prev_label if prev_label != "unknown" else label)
        else:
            out.append(run)
    if len(out) >= 2 and (out[-1][1] - out[-1][0]) < MIN_RESEGMENT_RUN_S:
        _last_start, last_end, last_label = out.pop()
        prev_start, _, prev_label = out[-1]
        out[-1] = (prev_start, last_end, prev_label if prev_label != "unknown" else last_label)
    return out


def _split_dialogue_turns(text: str) -> list[str]:
    parts: list[str] = []
    for raw in _DIALOGUE_TURN_RE.split(text):
        part = raw.strip()
        if part.startswith("-"):
            part = part[1:].strip()
        if part:
            parts.append(part)
    return parts or [text.strip()]


def _normalize_source_segments(segments: list[dict]) -> list[dict]:
    """Collapse rows produced by a previous resegmentation pass.

    Diarization should be idempotent: rerunning it on a session must not keep
    splitting already-split rows. We group consecutive ``base-rNN`` rows back
    into their original source id, reconstructing explicit dialogue markers
    only when the previous chunk looks like a complete turn.
    """
    out: list[dict] = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        sid = str(seg.get("segment_id") or "")
        match = _RESEGMENTED_ID_RE.match(sid)
        if match is None:
            out.append(seg)
            i += 1
            continue

        base = match.group("base")
        group = [seg]
        i += 1
        while i < len(segments):
            next_sid = str(segments[i].get("segment_id") or "")
            next_match = _RESEGMENTED_ID_RE.match(next_sid)
            if next_match is None or next_match.group("base") != base:
                break
            group.append(segments[i])
            i += 1

        first = group[0]
        last = group[-1]
        merged = dict(first)
        merged["segment_id"] = base
        merged["started_at"] = first.get("started_at")
        merged["ended_at"] = last.get("ended_at")
        merged["orig_text"] = _join_resegmented_text(
            [str(row.get("orig_text") or "") for row in group]
        )
        out.append(merged)
    return out


def _join_resegmented_text(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    out = cleaned[0]
    for part in cleaned[1:]:
        sep = " -" if _looks_like_turn_end(out) else " "
        out = f"{out.rstrip()}{sep}{part}"
    return out


def _looks_like_turn_end(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped.endswith("--"):
        return True
    return stripped[-1] in _TURN_END_CHARS


def _time_chunks_for_text_parts(
    t0: float,
    t1: float,
    parts: list[str],
) -> list[tuple[float, float, str]]:
    if len(parts) <= 1 or t1 <= t0:
        return [(t0, t1, parts[0] if parts else "")]
    weights = [max(1, len(part)) for part in parts]
    total = sum(weights)
    out: list[tuple[float, float, str]] = []
    cur = t0
    for i, (part, weight) in enumerate(zip(parts, weights, strict=True)):
        nxt = t1 if i == len(parts) - 1 else cur + (t1 - t0) * weight / total
        out.append((cur, nxt, part))
        cur = nxt
    return out


def _resegment_transcript(
    segments: list[dict],
    speech_segments: list[_SpeechSegment],
    labeled_windows: list[_LabeledWindow],
    *,
    single_speaker: bool,
) -> list[dict[str, Any]]:
    by_id = {seg.segment_id: seg for seg in speech_segments}
    rows: list[dict[str, Any]] = []

    def append_row(source: dict, source_id: str, idx: int, total: int,
                   t0: float, t1: float, text: str, speaker: str) -> None:
        text = text.strip()
        if not text:
            return
        segment_id = source_id if total == 1 else f"{source_id}-r{idx:02d}"
        rows.append(
            {
                "segment_id": segment_id,
                "started_at": t0,
                "ended_at": t1,
                "orig_text": text,
                "orig_status": "final",
                "orig_lang": source.get("orig_lang"),
                "trans_text": "",
                "trans_status": "skipped",
                "trans_lang": source.get("trans_lang"),
                "speaker": speaker,
            }
        )

    for source in segments:
        source_id = source["segment_id"]
        speech = by_id.get(source_id)
        if speech is None:
            continue
        text = (source.get("orig_text") or "").strip()
        if not text:
            continue

        parts = _split_dialogue_turns(text)
        chunks: list[tuple[float, float, str, str]] = []
        if len(parts) > 1:
            runs = _speaker_runs_for_interval(
                speech.speech_t0,
                speech.speech_t1,
                labeled_windows,
                single_speaker=single_speaker,
            )
            if len(runs) == len(parts):
                for (part_t0, part_t1, speaker_label), part in zip(
                    runs, parts, strict=True
                ):
                    chunks.append((part_t0, part_t1, part, speaker_label))
            else:
                for part_t0, part_t1, part in _time_chunks_for_text_parts(
                    speech.speech_t0,
                    speech.speech_t1,
                    parts,
                ):
                    speaker_label = _label_for_interval(
                        part_t0,
                        part_t1,
                        labeled_windows,
                        single_speaker=single_speaker,
                    )
                    chunks.append((part_t0, part_t1, part, speaker_label))
        else:
            # Without explicit text turn markers, the speaker timeline alone
            # is not enough to place a safe text boundary. Keep the transcript
            # row intact and label it by the dominant speaker instead of
            # cutting through a sentence.
            speaker_label = _label_for_interval(
                speech.speech_t0,
                speech.speech_t1,
                labeled_windows,
                single_speaker=single_speaker,
            )
            chunks.append((speech.orig_t0, speech.orig_t1, text, speaker_label))

        total = len(chunks)
        for idx, (chunk_t0, chunk_t1, chunk_text, speaker_label) in enumerate(chunks):
            append_row(
                source,
                source_id,
                idx,
                total,
                chunk_t0,
                chunk_t1,
                chunk_text,
                speaker_label,
            )

    for ord_, row in enumerate(rows):
        row["ord"] = ord_
    return rows


async def diarize_session(
    *,
    wav_path: Path,
    segments: list[dict],
    speaker: SpeakerBackend,
    on_progress: Callable[[float, str | None], None] | None = None,
) -> DiarizeResult:
    """Build speaker-aware transcript rows. See module docstring."""
    if not wav_path.exists():
        raise FileNotFoundError(f"WAV not found: {wav_path}")
    segments = _normalize_source_segments(segments)
    samples, sr = _read_wav_int16_mono(wav_path)

    def report(frac: float, log_line: str | None = None) -> None:
        if on_progress is not None:
            on_progress(frac, log_line)

    speech_segments, out_labels = _collect_speech_segments(samples, sr, segments)

    # ---- Pass 1: embed whole-session speech windows ----
    windows = await _embed_session_windows(
        samples=samples,
        sr=sr,
        speech_segments=speech_segments,
        speaker=speaker,
        on_progress=on_progress,
    )

    if not windows:
        report(1.0, "No embeddable segments")
        rows = _resegment_transcript(
            segments,
            speech_segments,
            [],
            single_speaker=False,
        )
        return DiarizeResult(
            labels={row["segment_id"]: row["speaker"] for row in rows},
            segments=rows,
        )

    # ---- Pass 2: cluster windows into a session-level speaker timeline ----
    k, labeled_windows = _cluster_windows(windows)
    inferred = _assign_segments_from_timeline(
        speech_segments,
        labeled_windows,
        single_speaker=k <= 1,
    )
    out_labels.update(inferred)
    rows = _resegment_transcript(
        segments,
        speech_segments,
        labeled_windows,
        single_speaker=k <= 1,
    )
    final_labels = {row["segment_id"]: row["speaker"] for row in rows}

    distinct = {v for v in final_labels.values() if v.startswith("Speaker ")}
    n_unknown = sum(1 for v in final_labels.values() if v == "unknown")
    log.info(
        "diarize: %d windows → %d speaker(s), %d segment(s), %d unknown",
        len(windows), len(distinct), len(rows), n_unknown,
    )
    report(
        1.0,
        f"Detected {len(distinct)} speakers from {len(windows)} window(s)"
        + (f"; {n_unknown} unknown" if n_unknown else ""),
    )
    return DiarizeResult(labels=final_labels, segments=rows)


def _window_starts(seg_dur: float, win: float, hop: float) -> list[float]:
    """Overlapping window start offsets that always cover the full segment.

    Returns at least one window. The last window is shifted left if needed
    so its end aligns with seg_dur (no truncated tail samples)."""
    if seg_dur < win:
        return [0.0]
    starts: list[float] = []
    t = 0.0
    while t + win <= seg_dur + 1e-9:
        starts.append(t)
        t += hop
    if not starts:
        starts.append(0.0)
    last_end = starts[-1] + win
    if last_end < seg_dur - 1e-3:
        starts.append(max(0.0, seg_dur - win))
    return starts
