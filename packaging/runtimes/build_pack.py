#!/usr/bin/env python3
"""Build one Wrenote compute runtime pack.

A pack is a zip the engine downloads on demand (see
``engine/wrenote/core/runtimes.py``) containing the native inference bindings
compiled for one accelerator::

    MANIFEST.json
    site-packages/   pip-installed bindings, --no-deps (the app ships the deps)
    bin/             optional extra shared libraries (CUDA runtime DLLs, …)

Example (Windows runner, Vulkan)::

    set CMAKE_ARGS=-DGGML_VULKAN=on
    set GGML_VULKAN=1
    python packaging/runtimes/build_pack.py --variant vulkan --version 2026.09.02 ^
        --spec llama-cpp-python==0.3.28 --spec pywhispercpp==1.4.1 --out dist/runtimes

The platform tag and Python version default to the interpreter running this
script, which must be the same Python minor the frozen engine uses (3.11).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform as _stdplatform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SCHEMA = 1


def platform_tag() -> str:
    m = _stdplatform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(m, m)
    return f"{sys.platform}-{arch}"


def python_tag() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def pip_install(target: Path, specs: list[str], *, extra_index_url: str | None,
                with_deps: bool, python: str) -> None:
    cmd = [python, "-m", "pip", "install", "--target", str(target), "--no-cache-dir",
           "--upgrade", *specs]
    if not with_deps:
        cmd.append("--no-deps")
    if extra_index_url:
        cmd += ["--extra-index-url", extra_index_url]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def installed_packages(site: Path) -> dict[str, str]:
    """``{distribution name: version}`` from the .dist-info dirs in ``site``."""
    out: dict[str, str] = {}
    for info in sorted(site.glob("*.dist-info")):
        name, _, version = info.name[: -len(".dist-info")].rpartition("-")
        if name:
            out[name] = version
    return out


def top_level_modules(site: Path) -> list[str]:
    """Import names the pack provides: ``top_level.txt`` where present, else
    the package dirs / modules found next to the dist-info."""
    names: set[str] = set()
    for info in site.glob("*.dist-info"):
        tl = info / "top_level.txt"
        if tl.exists():
            names.update(line.strip() for line in tl.read_text().splitlines() if line.strip())
            continue
        record = info / "RECORD"
        if record.exists():
            for line in record.read_text().splitlines():
                first = line.split(",", 1)[0].split("/", 1)[0]
                if first and not first.endswith(".dist-info") and not first.startswith("_"):
                    names.add(first.split(".", 1)[0])
    if not names:
        for entry in site.iterdir():
            if entry.name.endswith(".dist-info") or entry.name == "__pycache__":
                continue
            names.add(entry.name.split(".", 1)[0])
    return sorted(names)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def build(*, variant: str, version: str, specs: list[str], out_dir: Path,
          extra_index_url: str | None = None, bins: list[Path] | None = None,
          with_deps: bool = False, python: str = sys.executable,
          tag: str | None = None, py: str | None = None,
          extra_modules: list[str] | None = None) -> Path:
    tag = tag or platform_tag()
    py = py or python_tag()
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"wrenote-runtime-{variant}-"))
    try:
        site = stage / "site-packages"
        site.mkdir()
        pip_install(site, specs, extra_index_url=extra_index_url, with_deps=with_deps, python=python)
        shutil.rmtree(site / "bin", ignore_errors=True)  # console scripts, if any
        if bins:
            bin_dir = stage / "bin"
            bin_dir.mkdir()
            for b in bins:
                shutil.copy2(b, bin_dir / b.name)
        modules = sorted(set(top_level_modules(site)) | set(extra_modules or []))
        manifest = {
            "schema": SCHEMA,
            "variant": variant,
            "platform_tag": tag,
            "python": py,
            "version": version,
            "modules": modules,
            "packages": installed_packages(site),
            "built_at": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
        }
        (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        name = f"wrenote-runtime-{variant}-{tag}-py{py}.zip"
        archive = out_dir / name
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for path in sorted(stage.rglob("*")):
                if path.is_dir() or "__pycache__" in path.parts:
                    continue
                z.write(path, path.relative_to(stage).as_posix())
        digest = sha256_of(archive)
        (out_dir / (name + ".sha256")).write_text(f"{digest}  {name}\n")
        print(f"built {archive} ({archive.stat().st_size >> 20} MB, sha256 {digest[:12]}…)")
        print(f"  modules: {modules}")
        print(f"  packages: {manifest['packages']}")
        return archive
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True, choices=["cpu", "metal", "cuda", "vulkan"])
    ap.add_argument("--version", required=True, help="pack version, e.g. 2026.09.02")
    ap.add_argument("--spec", action="append", required=True, help="pip requirement (repeatable)")
    ap.add_argument("--out", type=Path, default=Path("dist/runtimes"))
    ap.add_argument("--extra-index-url")
    ap.add_argument("--bin", action="append", type=Path, default=[], help="extra shared library to ship in bin/")
    ap.add_argument("--with-deps", action="store_true", help="also install dependencies (default: --no-deps)")
    ap.add_argument("--python", default=sys.executable, help="interpreter whose pip builds the pack")
    ap.add_argument("--module", action="append", default=[], help="extra top-level module to route (repeatable)")
    args = ap.parse_args(argv)
    for b in args.bin:
        if not b.exists():
            print(f"--bin {b}: not found", file=sys.stderr)
            return 2
    build(variant=args.variant, version=args.version, specs=args.spec, out_dir=args.out,
          extra_index_url=args.extra_index_url, bins=args.bin, with_deps=args.with_deps,
          python=args.python, extra_modules=args.module)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    sys.exit(main())
