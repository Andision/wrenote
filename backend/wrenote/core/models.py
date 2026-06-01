"""Model manifest + first-run downloader.

Heavy model weights are NOT bundled with the app; they live in
``~/.wrenote/models/`` and are fetched on first run. The set of required models
is derived from the active config (whichever STT / translator / chat backends
need a local file), so a ``mock`` backend requires nothing.

Downloads stream to a ``.partial`` file with HTTP ``Range`` resume and an atomic
rename on completion, so a killed download resumes instead of leaving a corrupt
model that only fails later at load time. Set ``HF_ENDPOINT`` (e.g.
``https://hf-mirror.com``) to use a HuggingFace mirror.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

CHUNK = 1 << 20  # 1 MiB streaming chunk


def _hf_base() -> str:
    """HuggingFace base URL; override with HF_ENDPOINT for a mirror."""
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


# filename -> (HF repo path after /<base>/, approx size in bytes for weighting).
# Approx sizes come from the published files; the real total is taken from the
# server's Content-Length at download time.
_KNOWN: dict[str, tuple[str, int]] = {
    "ggml-large-v3-turbo-q5_0.bin": (
        "ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        574_041_195,
    ),
    "Hy-MT2-1.8B-Q4_K_M.gguf": (
        "tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q4_K_M.gguf",
        1_133_080_448,
    ),
    "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf": (
        "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/"
        "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        2_497_280_736,
    ),
    # ECAPA-TDNN speaker embedding, exported from speechbrain to ONNX (torch-free).
    "spkrec-ecapa-voxceleb.onnx": (
        "Andision/wrenote-models/resolve/main/spkrec-ecapa-voxceleb.onnx",
        84_083_886,
    ),
}


@dataclass(frozen=True)
class ModelEntry:
    key: str  # "stt" | "translator" | "chat"
    path: Path  # where it must live (from config)
    repo_path: str  # HF path after the base
    approx_size: int

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def url(self) -> str:
        return f"{_hf_base()}/{self.repo_path}"

    @property
    def partial(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".partial")

    @property
    def present(self) -> bool:
        # "Present" = full file exists and is at least ~99% of the expected size
        # (guards against a truncated/partial left behind as the final name).
        return self.path.exists() and self.path.stat().st_size >= int(self.approx_size * 0.99)

    def status_dict(self) -> dict[str, object]:
        downloaded = 0
        if self.path.exists():
            downloaded = self.path.stat().st_size
        elif self.partial.exists():
            downloaded = self.partial.stat().st_size
        return {
            "key": self.key,
            "filename": self.filename,
            "present": self.present,
            "size": self.approx_size,
            "downloaded": downloaded,
        }


def _entry_for(key: str, model_path: str | None) -> ModelEntry | None:
    if not model_path:
        return None
    path = Path(model_path).expanduser()
    known = _KNOWN.get(path.name)
    if known is None:
        # A custom / unknown model the user pointed at; we can't auto-download it.
        log.warning("No download source known for %s (%s); skipping", key, path.name)
        return None
    repo_path, size = known
    return ModelEntry(key=key, path=path, repo_path=repo_path, approx_size=size)


def required_models(cfg: Config) -> list[ModelEntry]:
    """Models the active config needs as local files (mock backends need none)."""
    entries: list[ModelEntry] = []
    if cfg.stt.backend == "whisper_cpp":
        entries.append(_entry_for("stt", cfg.stt.params.get("model_path")))
    if cfg.translator.backend == "llama_cpp":
        entries.append(_entry_for("translator", cfg.translator.params.get("model_path")))
    if cfg.chat.backend == "llama_cpp":
        entries.append(_entry_for("chat", cfg.chat.params.get("model_path")))
    if cfg.speaker.backend == "ecapa":
        entries.append(_entry_for("speaker", cfg.speaker.params.get("model_path")))
    return [e for e in entries if e is not None]


async def download_model(
    entry: ModelEntry,
    on_progress: Callable[[float, str], None],
) -> None:
    """Stream a model to its ``.partial`` then atomically rename into place.

    Resumes from a prior ``.partial`` via a ``Range`` request. ``on_progress`` is
    called from the event-loop thread with (fraction_in_[0,1], human_status).
    """
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    partial = entry.partial
    have = partial.stat().st_size if partial.exists() else 0

    def _open() -> urllib.request.addinfourl:
        req = urllib.request.Request(entry.url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        return urllib.request.urlopen(req, timeout=30)  # HF / mirror, https

    resp = await asyncio.to_thread(_open)
    # Total = bytes already on disk + what the server will send now. If the server
    # ignored our Range (200 not 206), restart from scratch.
    resuming = resp.status == 206
    remaining = int(resp.headers.get("Content-Length") or 0)
    if have and not resuming:
        have = 0  # server sent the whole file; overwrite
    total = (have + remaining) if remaining else entry.approx_size

    mode = "ab" if (have and resuming) else "wb"
    done = have if resuming else 0
    mb = 1 << 20
    try:
        with open(partial, mode) as f:
            while True:
                chunk = await asyncio.to_thread(resp.read, CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                frac = min(0.999, done / total) if total else 0.0
                on_progress(frac, f"{done // mb}/{total // mb} MB")
    finally:
        resp.close()

    if total and done < int(total * 0.99):
        raise RuntimeError(
            f"{entry.filename}: download truncated ({done}/{total} bytes)"
        )
    os.replace(partial, entry.path)  # atomic
    on_progress(1.0, f"{entry.filename} ready")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()
