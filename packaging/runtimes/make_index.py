#!/usr/bin/env python3
"""Write ``runtimes.json`` — the index the engine reads to find runtime packs.

Scans a directory of ``wrenote-runtime-*.zip`` files (built by
``build_pack.py``), reads each pack's ``MANIFEST.json`` and emits::

    {"schema": 1, "generated_at": "...", "packs": [
      {"variant": "vulkan", "platform_tag": "win32-x86_64", "python": "3.11",
       "version": "2026.09.02", "url": "<base>/wrenote-runtime-vulkan-win32-x86_64-py3.11.zip",
       "sha256": "…", "size": 123456789, "modules": ["llama_cpp", "pywhispercpp"]}
    ]}

``--base-url`` is where the zips will be served from (a GitHub release's
download URL). The engine filters by its own platform tag and Python minor.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import zipfile
from pathlib import Path

from build_pack import sha256_of  # same directory

SCHEMA = 1


def entry_for(archive: Path, base_url: str) -> dict:
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("MANIFEST.json").decode("utf-8"))
    return {
        "variant": manifest["variant"],
        "platform_tag": manifest["platform_tag"],
        "python": manifest.get("python", ""),
        "version": manifest.get("version", ""),
        "url": f"{base_url.rstrip('/')}/{archive.name}",
        "sha256": sha256_of(archive),
        "size": archive.stat().st_size,
        "modules": manifest.get("modules", []),
        "packages": manifest.get("packages", {}),
    }


def make_index(archives: list[Path], base_url: str) -> dict:
    packs = [entry_for(a, base_url) for a in sorted(archives)]
    return {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
        "packs": packs,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=Path("dist/runtimes"), help="where the zips are")
    ap.add_argument("--base-url", required=True, help="public URL prefix the zips will be served from")
    ap.add_argument("--out", type=Path, default=None, help="default: <dir>/runtimes.json")
    args = ap.parse_args(argv)
    archives = sorted(args.dir.glob("wrenote-runtime-*.zip"))
    if not archives:
        print(f"no wrenote-runtime-*.zip in {args.dir}", file=sys.stderr)
        return 2
    index = make_index(archives, args.base_url)
    out = args.out or (args.dir / "runtimes.json")
    out.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} with {len(index['packs'])} pack(s)")
    for p in index["packs"]:
        print(f"  {p['variant']:7} {p['platform_tag']:14} py{p['python']} {p['size'] >> 20:5} MB  {p['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
