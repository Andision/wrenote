"""What Wrenote is, and what it is built on — Settings → About.

The Python dependencies are read from the installed distributions at request
time rather than listed anywhere: a hand-kept list of licences goes stale
silently, and a licence stated from memory is worse than no licence at all.
Everything metadata cannot answer for — the native libraries, the front-end
packages, the model weights — is in ``wrenote/credits.yaml`` and
``engine/models.yaml``, each with the upstream page where the terms live.
"""
from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends

from ..core.catalogue import ModelCatalogue
from ..deps import get_catalogue

log = logging.getLogger(__name__)
router = APIRouter()

#: Wrenote's own licence, as declared by every manifest in the repo.
APP_LICENSE = "AGPL-3.0-only"
APP_SOURCE = "https://github.com/Andision/wrenote"

#: The runtime Python dependencies, in the order a reader would want them.
#: Not `pyproject`'s list parsed at runtime: this is the shipped set, and the
#: extras that are only present on some platforms are marked as such below.
_PY_PACKAGES = (
    "fastapi", "uvicorn", "pydantic", "pydantic-settings", "PyYAML", "numpy",
    "scikit-learn", "soundfile", "python-multipart", "aiosqlite", "onnxruntime",
    "certifi", "pywhispercpp", "llama-cpp-python", "sherpa-onnx", "torch",
)


def _credits_path() -> Path:
    """Beside the package in a checkout; beside config.yaml when frozen."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "credits.yaml"
    return Path(__file__).resolve().parent.parent / "credits.yaml"


def _python_packages() -> list[dict[str, str]]:
    """Name, version and licence of each dependency that is actually here."""
    out: list[dict[str, str]] = []
    for name in _PY_PACKAGES:
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            continue  # a platform-specific extra that this build doesn't have
        meta = dist.metadata
        license_ = (meta.get("License-Expression") or "").strip()
        if not license_:
            # Older metadata puts it in `License`, sometimes as whole text;
            # the classifier is the reliable short form when it does.
            classifiers = [
                c.rsplit("::", 1)[-1].strip()
                for c in meta.get_all("Classifier") or []
                if c.startswith("License ::")
            ]
            raw = (meta.get("License") or "").strip()
            license_ = classifiers[0] if classifiers else raw.splitlines()[0] if raw else ""
        out.append({
            "name": meta["Name"] or name,
            "version": dist.version,
            "license": license_[:60],
        })
    return out


@router.get("/about")
async def about(catalogue: ModelCatalogue = Depends(get_catalogue)) -> dict[str, Any]:
    """The app's own licence, and everything it ships or downloads."""
    from .. import __version__ as version

    try:
        curated = yaml.safe_load(_credits_path().read_text(encoding="utf-8")) or {}
    except Exception:
        log.exception("credits.yaml could not be read")
        curated = {}

    models = [
        {
            "name": spec.name,
            "license": spec.license,
            "url": spec.url,
            "kind": spec.kind,
        }
        for spec in catalogue
        if spec.url or spec.license
    ]
    return {
        "version": version,
        "license": APP_LICENSE,
        "source": APP_SOURCE,
        "native": curated.get("native") or [],
        "web": curated.get("web") or [],
        "python": _python_packages(),
        "models": models,
    }
