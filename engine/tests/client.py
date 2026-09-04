"""WebSocket integration test client.

Connects to a running wrenote server, sends audio (silence, a WAV file,
or any audio file ffmpeg can decode — MP3/M4A/OGG/etc.), prints every
server event. Used for protocol validation without involving a browser.

Usage:
    python tests/client.py                         # 10 chunks of silent PCM
    python tests/client.py audio.wav               # stream a real WAV
    python tests/client.py audio.mp3 --duration 30 # stream first 30s of MP3
    python tests/client.py audio.mp3 --max-seg 8000  # set server max_segment_ms

The server must be running (e.g. ``./scripts/dev.sh``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import websockets

DEFAULT_URL = "ws://127.0.0.1:8000/ws"
SAMPLE_RATE = 16_000


def decode_to_pcm(path: Path, *, duration_s: float | None = None) -> bytes:
    """Decode any audio file to 16kHz mono int16 PCM via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout


async def feed_silence(
    send_func,
    *,
    num_chunks: int = 10,
    chunk_ms: int = 100,
) -> None:
    bytes_per_chunk = SAMPLE_RATE * chunk_ms // 1000 * 2
    silent_pcm = b"\x00" * bytes_per_chunk
    for _ in range(num_chunks):
        await send_func(silent_pcm)
        await asyncio.sleep(chunk_ms / 1000)


async def feed_pcm(send_func, pcm: bytes, *, chunk_ms: int = 100) -> None:
    """Stream raw int16 PCM in real-time-paced chunks."""
    bytes_per_chunk = SAMPLE_RATE * chunk_ms // 1000 * 2
    for start in range(0, len(pcm), bytes_per_chunk):
        chunk = pcm[start : start + bytes_per_chunk]
        if not chunk:
            break
        await send_func(chunk)
        await asyncio.sleep(chunk_ms / 1000)


async def run(
    url: str,
    audio_path: Path | None,
    chunk_ms: int,
    src_lang: str,
    tgt_lang: str,
    duration_s: float | None,
    min_silence_ms: int,
    max_segment_ms: int,
    partial_interval_ms: int,
) -> int:
    print(f"Connecting to {url}")
    async with websockets.connect(url) as ws:
        start_msg = {
            "type": "start",
            "config": {
                "src": src_lang,
                "tgt": tgt_lang,
                "min_silence_ms": min_silence_ms,
                "max_segment_ms": max_segment_ms,
                "partial_interval_ms": partial_interval_ms,
            },
        }
        await ws.send(json.dumps(start_msg))
        print(f"→ {start_msg}")

        received: list[dict] = []

        async def receive_loop() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    received.append(msg)
                    seg = msg.get("segment_id", "")
                    text = msg.get("text", "")
                    spk = msg.get("speaker")
                    extra = f" [{seg[:8]}]" if seg else ""
                    if spk:
                        extra += f" <{spk}>"
                    extra += f" {text!r}" if text else ""
                    print(f"← {msg.get('type', '?'):14}{extra}")
            except websockets.ConnectionClosed:
                pass

        recv_task = asyncio.create_task(receive_loop())

        if audio_path:
            print(f"Decoding {audio_path} via ffmpeg …")
            pcm = decode_to_pcm(audio_path, duration_s=duration_s)
            total_s = len(pcm) / 2 / SAMPLE_RATE
            print(f"  → {len(pcm)} bytes ({total_s:.1f}s); streaming in {chunk_ms}ms chunks")
            await feed_pcm(ws.send, pcm, chunk_ms=chunk_ms)
        else:
            print(f"Streaming 10 chunks of silent PCM ({chunk_ms}ms each)")
            await feed_silence(ws.send, num_chunks=10, chunk_ms=chunk_ms)

        # Allow trailing segments (incl. force-close) + STT + translation to drain
        drain_s = max(3.0, max_segment_ms / 1000 * 1.5)
        print(f"Draining for up to {drain_s:.1f}s…")
        await asyncio.sleep(drain_s)

        try:
            await ws.send(json.dumps({"type": "stop"}))
            print("→ stop")
        except websockets.ConnectionClosed:
            print("(connection already closed by server)")

        # Wait up to 8s for server-side flush (which transcribes + translates
        # any in-flight segment after stop). End early if server closes the
        # connection (it does so after the flush completes).
        try:
            await asyncio.wait_for(recv_task, timeout=8.0)
        except TimeoutError:
            print("(stop drain: 8s timeout — server may not have flushed)")
            recv_task.cancel()
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    types_seen = [m.get("type") for m in received]
    counts = {t: types_seen.count(t) for t in set(types_seen)}
    print(f"\n--- Summary: {len(received)} events ---")
    for t, n in sorted(counts.items()):
        print(f"  {t}: {n}")

    # Print every final transcript for readability
    finals = [m for m in received if m.get("type") == "final"]
    if finals:
        print(f"\n--- Final transcripts ({len(finals)}) ---")
        for f in finals:
            print(f"  [{f['segment_id'][:8]}] ({f.get('t0',0):.1f}-{f.get('t1',0):.1f}s) {f['text']}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, nargs="?", help="Audio file (any format ffmpeg supports)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket URL (default {DEFAULT_URL})")
    parser.add_argument("--chunk-ms", type=int, default=100, help="Chunk size in ms")
    parser.add_argument("--src", default="en", help="Source language (default en)")
    parser.add_argument("--tgt", default="zh", help="Target language (default zh)")
    parser.add_argument("--duration", type=float, default=None, help="Limit input audio to N seconds")
    parser.add_argument("--min-silence", type=int, default=500, help="Server min_silence_ms")
    parser.add_argument("--max-seg", type=int, default=8000, help="Server max_segment_ms")
    parser.add_argument(
        "--partial-interval", type=int, default=800,
        help="Server partial_interval_ms (how often to emit partials; 0 disables)",
    )
    args = parser.parse_args()

    return asyncio.run(
        run(
            args.url, args.audio, args.chunk_ms,
            args.src, args.tgt,
            args.duration, args.min_silence, args.max_seg,
            args.partial_interval,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
