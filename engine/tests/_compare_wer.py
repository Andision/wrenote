"""Compare Whisper transcription against the SRT ground truth.

Runs whisper.cpp on a fixed window of the MP3, parses the SRT to extract
the matching window, and computes WER (word error rate) + side-by-side diff.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import time
from pathlib import Path

import jiwer

import wrenote  # noqa: F401 -- registers backends
from wrenote.core.events import AudioSegment
from wrenote.core.registry import make_stt

MP3 = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.mp3")
SRT = Path("/Users/andision/Workspaces/interpreter/miscs/conversation_example_1.srt")
MODEL = Path("~/.wrenote/models/ggml-large-v3-turbo-q5_0.bin").expanduser()
DURATION_S = 60.0


def parse_srt(srt_path: Path) -> list[tuple[float, float, str]]:
    """Return list of (start_s, end_s, text) cues."""
    blocks = re.split(r"\n\s*\n", srt_path.read_text(encoding="utf-8").strip())
    cues = []
    for blk in blocks:
        lines = blk.strip().splitlines()
        if len(lines) < 3:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1]
        )
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(lines[2:])
        cues.append((start, end, text))
    return cues


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace; keep apostrophes."""
    text = text.lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[*_]", " ", text)              # strip markdown-ish marks
    text = re.sub(r"[\-–—]", " ", text)            # turn dashes into spaces (speaker turns)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)      # keep letters + apostrophe
    text = re.sub(r"\s+", " ", text).strip()
    return text


def decode_mp3(path: Path, duration_s: float) -> bytes:
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-t", f"{duration_s:.3f}", "-f", "s16le", "-ac", "1", "-ar", "16000", "-",
    ]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


async def main() -> None:
    print("Loading SRT…")
    cues = parse_srt(SRT)
    truth_cues = [c for c in cues if c[0] < DURATION_S]
    truth_raw = " ".join(c[2] for c in truth_cues)
    truth = normalize(truth_raw)
    print(f"  SRT cues in first {DURATION_S}s: {len(truth_cues)}  ({len(truth.split())} words)")

    print(f"\nDecoding {MP3.name} first {DURATION_S}s …")
    pcm = decode_mp3(MP3, DURATION_S)

    print("\nLoading whisper.cpp model …")
    stt = make_stt("whisper_cpp", {"model_path": str(MODEL), "language": "en"})
    t0 = time.monotonic()
    await stt.load()
    print(f"  loaded in {time.monotonic()-t0:.1f}s")

    seg = AudioSegment(segment_id="t", pcm=pcm, t0=0.0, t1=DURATION_S)
    print(f"\nTranscribing {DURATION_S}s …")
    t1 = time.monotonic()
    event = await stt.transcribe_segment(seg, src_lang="en")
    dt = time.monotonic() - t1
    print(f"  inference: {dt:.2f}s (RTF={dt/DURATION_S:.3f}, {DURATION_S/dt:.1f}× realtime)")

    hyp = normalize(event.text)
    print(f"  whisper hypothesis: {len(hyp.split())} words")

    # WER via jiwer.process_words (jiwer >= 3.x API)
    out = jiwer.process_words(truth, hyp)
    cer = jiwer.cer(truth, hyp)
    total_ref = out.hits + out.substitutions + out.deletions
    print("\n--- WER metrics ---")
    print(f"  WER (word error rate): {out.wer*100:.2f}%")
    print(f"  CER (char error rate): {cer*100:.2f}%")
    print(f"  Hits:          {out.hits}")
    print(f"  Substitutions: {out.substitutions}")
    print(f"  Insertions:    {out.insertions}")
    print(f"  Deletions:     {out.deletions}")
    print(f"  Total ref words: {total_ref}")

    # Aligned diff (show first 100 word-level alignments)
    print("\n--- Side-by-side (truth vs whisper) ---")
    truth_words = out.references[0]
    hyp_words = out.hypotheses[0]
    alignments = out.alignments[0]
    ref_idx = 0
    hyp_idx = 0
    lines = []
    for chunk in alignments:
        op = chunk.type
        ref_chunk = truth_words[chunk.ref_start_idx:chunk.ref_end_idx]
        hyp_chunk = hyp_words[chunk.hyp_start_idx:chunk.hyp_end_idx]
        if op == "equal":
            lines.append(("=", " ".join(ref_chunk), ""))
        elif op == "substitute":
            for r, h in zip(ref_chunk, hyp_chunk):
                lines.append(("S", r, h))
        elif op == "insert":
            lines.append(("I", "", " ".join(hyp_chunk)))
        elif op == "delete":
            lines.append(("D", " ".join(ref_chunk), ""))

    shown = 0
    for op, r, h in lines:
        if op == "=" and shown >= 60:
            continue  # skip long equal stretches
        if op == "=":
            print(f"  =   {r}")
            shown += len(r.split())
        else:
            print(f"  {op}   ref={r!r}   hyp={h!r}")

    print("\n--- Raw whisper text ---")
    print(event.text)
    print(f"\n--- Raw SRT text (cues 1..{len(truth_cues)}) ---")
    print(truth_raw)


if __name__ == "__main__":
    asyncio.run(main())
