"""Per-session raw-audio WAV writer.

Streams int16 16k mono PCM chunks straight to disk via the stdlib ``wave``
module — header is finalized on close. Files live at
``~/.wrenote/recordings/<session_id>.wav`` and are owned by the server
process. The frontend references them by the same session id it generated
locally; on session deletion the frontend calls the matching DELETE endpoint
on the server so files don't outlive their metadata.

Paused audio is *not* written: the WS pause handler stops PCM flow on the
client side, and this writer only ever sees what the WS actually delivers.
"""
from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("~/.wrenote/recordings").expanduser()


class WavWriter:
    """Thread-safe append-only WAV file for a single session."""

    def __init__(
        self,
        session_id: str,
        *,
        dir_: Path | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width_bytes: int = 2,
    ) -> None:
        target_dir = dir_ or DEFAULT_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        self._path = target_dir / f"{session_id}.wav"
        self._lock = threading.Lock()
        # Not a context manager: this handle lives as long as the recording
        # does, and close() belongs to the recorder's own lifecycle.
        self._wf: wave.Wave_write | None = wave.open(str(self._path), "wb")  # noqa: SIM115
        self._wf.setnchannels(channels)
        self._wf.setsampwidth(sample_width_bytes)
        self._wf.setframerate(sample_rate)
        self._bytes_written = 0
        log.info("Recording session audio to %s", self._path)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    def append(self, pcm: bytes) -> None:
        """Append one PCM chunk. No-op after close()."""
        if not pcm:
            return
        with self._lock:
            if self._wf is None:
                return
            self._wf.writeframes(pcm)
            self._bytes_written += len(pcm)

    def close(self) -> None:
        """Finalize the WAV header. Idempotent. Removes the file if empty
        — there's no point keeping a zero-length WAV around."""
        with self._lock:
            if self._wf is None:
                return
            try:
                self._wf.close()
            except Exception:
                log.exception("WAV close failed for %s", self._path)
            self._wf = None
        if self._bytes_written == 0:
            try:
                self._path.unlink(missing_ok=True)
                log.info("Removed empty recording %s", self._path)
            except Exception:
                log.exception("failed to remove empty recording %s", self._path)


def resolve_recording_path(session_id: str, *, dir_: Path | None = None) -> Path:
    """Where the WAV for this session lives. Caller checks existence."""
    return (dir_ or DEFAULT_DIR) / f"{session_id}.wav"
