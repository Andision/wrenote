"""First-run model downloader.

Heavy model weights are NOT bundled with the app; they live in the models
directory (``~/.wrenote/models/`` by default) and are fetched on first run.
*Which* models is decided by :mod:`wrenote.core.catalogue` from the active
config, so a ``mock`` backend requires nothing.

Downloads stream to a ``.partial`` file with HTTP ``Range`` resume and an atomic
rename on completion, so a killed download resumes instead of leaving a corrupt
model that only fails later at load time. When the catalogue knows the file's
sha256 it is verified before that rename: a truncated download, a corrupted
transfer or a file swapped upstream should fail here, loudly, rather than at
inference time as gibberish. Set ``HF_ENDPOINT`` (e.g. ``https://hf-mirror.com``)
to use a HuggingFace mirror.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import ssl
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .catalogue import ModelCatalogue, resolve_all

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

CHUNK = 1 << 20  # 1 MiB streaming chunk


@dataclass(frozen=True)
class ModelEntry:
    """One file to fetch, tied back to the catalogue entry that named it."""

    key: str  # the kind that needs it: "stt" | "translator" | "chat" | "speaker"
    path: Path  # where it must live
    url: str
    approx_size: int
    sha256: str = ""  # "" when upstream exposes no content hash
    model_id: str = ""
    model_name: str = ""

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def partial(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".partial")

    @property
    def present(self) -> bool:
        # Size, not hash: this is called on every status poll, and re-hashing
        # several GB to answer "is it there" would be absurd. The hash is
        # checked once, at the end of the download that produced the file.
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
            "model_id": self.model_id,
            "model_name": self.model_name,
        }


def required_models(cfg: Config, catalogue: ModelCatalogue | None = None) -> list[ModelEntry]:
    """Files the active config needs locally, in download order.

    A kind resolved to an explicit ``model_path`` contributes nothing: we don't
    know where a user's own file came from, so we can't fetch it.
    """
    cat = catalogue or ModelCatalogue.load()
    models_dir = Path(cfg.models.dir).expanduser()
    entries: list[ModelEntry] = []
    for kind, resolved in resolve_all(cfg, cat).items():
        spec = resolved.spec
        if spec is None:
            continue
        for f in spec.files:
            entries.append(ModelEntry(
                key=kind,
                path=f.local_path(models_dir),
                url=f.url,
                approx_size=f.size,
                sha256=f.sha256,
                model_id=spec.id,
                model_name=spec.name,
            ))
    return entries


def _ssl_context() -> ssl.SSLContext:
    """A verifying SSL context that works inside a PyInstaller-frozen app.

    Frozen builds ship no OpenSSL cert paths, so the default context can't find
    a CA bundle and every HTTPS download dies with CERTIFICATE_VERIFY_FAILED
    (notably on Windows). certifi — bundled with the app — supplies the roots.
    Falls back to the system default if certifi is somehow unavailable.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


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
        return urllib.request.urlopen(req, timeout=30, context=_ssl_context())  # HF / mirror, https

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
    if entry.sha256:
        on_progress(0.999, f"Verifying {entry.filename}")
        actual = await asyncio.to_thread(sha256_of, partial)
        if actual != entry.sha256:
            # Keep nothing: a file that hashes wrong is worse than no file,
            # because `present` (a size check) would accept it forever after.
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"{entry.filename}: checksum mismatch "
                f"(expected {entry.sha256[:12]}…, got {actual[:12]}…). "
                "The download was corrupted, or the file changed upstream."
            )
    os.replace(partial, entry.path)  # atomic
    on_progress(1.0, f"{entry.filename} ready")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()
