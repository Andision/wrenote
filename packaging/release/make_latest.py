#!/usr/bin/env python3
"""Write ``latest.json`` — the release index the engine's update check reads.

Given the installers a release ships, emit the file Tauri's updater plugin
expects (so the same index can drive an in-place install once builds are
signed) plus what our own check wants on top::

    {"version": "0.2.0", "notes": "…", "pub_date": "2026-09-05T00:00:00Z",
     "release_url": "https://github.com/Andision/wrenote/releases/tag/v0.2.0",
     "platforms": {
       "darwin-aarch64": {"url": "https://github.com/…/download/v0.2.0/Wrenote_0.2.0_aarch64.dmg",
                          "signature": ""},
       "windows-x86_64": {"url": "…/Wrenote_0.2.0_x64-setup.exe", "signature": ""}}}

Each installer is mapped to its platform key from the name Tauri gives it
(``_aarch64.dmg``, ``_x64-setup.exe``). A ``<installer>.sig`` next to it —
what ``tauri build`` writes when ``TAURI_SIGNING_PRIVATE_KEY`` is set — fills
``signature``; until signing is enabled that stays empty and the engine's
check ignores it. Unrecognised files are listed on stderr and left out, so a
stray artifact cannot become "the Windows build".

Usage (what build-tauri.yml runs)::

    python packaging/release/make_latest.py --version 0.2.0 --tag v0.2.0 \\
        --repo Andision/wrenote --dir dist/ --out dist/latest.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

INSTALLER_SUFFIXES = (".dmg", ".exe", ".msi", ".AppImage", ".deb", ".rpm")
_ARCH = {
    "aarch64": "aarch64", "arm64": "aarch64",
    "x64": "x86_64", "x86_64": "x86_64", "amd64": "x86_64",
    "x86": "i686", "i686": "i686",
}
_OS_BY_SUFFIX = {
    ".dmg": "darwin", ".exe": "windows", ".msi": "windows",
    ".AppImage": "linux", ".deb": "linux", ".rpm": "linux",
}


def platform_key(filename: str) -> str | None:
    """``Wrenote_0.2.0_aarch64.dmg`` → ``darwin-aarch64``; ``None`` if unsure."""
    suffix = next((s for s in INSTALLER_SUFFIXES if filename.endswith(s)), None)
    if suffix is None:
        return None
    stem = filename[: -len(suffix)]
    tokens = [t.lower() for t in re.split(r"[_\-.]", stem)]
    arch = next((_ARCH[t] for t in tokens if t in _ARCH), None)
    return f"{_OS_BY_SUFFIX[suffix]}-{arch}" if arch else None


def make_latest(
    installers: list[Path], *, version: str, tag: str, repo: str, notes: str = "",
    pub_date: str | None = None,
) -> dict:
    base = f"https://github.com/{repo}/releases/download/{tag}"
    platforms: dict[str, dict[str, str]] = {}
    for path in sorted(installers):
        key = platform_key(path.name)
        if key is None:
            print(f"skipping {path.name}: not an installer I can place", file=sys.stderr)
            continue
        if key in platforms:
            raise SystemExit(f"two installers for {key}: {platforms[key]['url']} and {path.name}")
        sig = path.with_name(path.name + ".sig")
        platforms[key] = {
            "url": f"{base}/{path.name}",
            "signature": sig.read_text(encoding="utf-8").strip() if sig.exists() else "",
        }
    if not platforms:
        raise SystemExit("no installers found; refusing to publish an empty index")
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date or _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "release_url": f"https://github.com/{repo}/releases/tag/{tag}",
        "platforms": platforms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True, help="the app version, e.g. 0.2.0")
    parser.add_argument("--tag", required=True, help="the release tag the files are attached to, e.g. v0.2.0")
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    parser.add_argument("--dir", type=Path, required=True, help="directory holding the installers")
    parser.add_argument("--notes-file", type=Path, help="text for `notes` (optional)")
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    args = parser.parse_args(argv)

    if (args.tag[1:] if args.tag.startswith("v") else args.tag) != args.version:
        raise SystemExit(f"--tag {args.tag} does not match --version {args.version}")
    installers = [p for p in args.dir.iterdir() if p.is_file() and p.name.endswith(INSTALLER_SUFFIXES)]
    notes = args.notes_file.read_text(encoding="utf-8").strip() if args.notes_file else ""
    index = make_latest(installers, version=args.version, tag=args.tag, repo=args.repo, notes=notes)
    text = json.dumps(index, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({', '.join(index['platforms'])})", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
