#!/usr/bin/env python3
"""The app version, kept in one place by keeping every place in agreement.

Wrenote's version is written in six manifests — the engine's, the Tauri
shell's (three files), the Electron shell's — because each toolchain reads its
own and none can read another's. Rather than a generator nobody runs, this
script *sets* all of them at once and *checks* that they agree, and CI runs
the check on every push, so drift fails a build instead of shipping a Tauri
app that reports 0.2.0 over an engine that says 0.1.0.

    python packaging/release/version.py            # print the version
    python packaging/release/version.py set 0.2.0  # rewrite every manifest
    python packaging/release/version.py --check    # exit 1 unless they all agree
    python packaging/release/version.py --check --tag v0.2.0   # … and match the tag

The engine reports this version at ``GET /v1/info`` and compares it with the
release index (core/update.py), so a release whose tag disagrees with its
manifests would tell every user to "update" to what they already run. The
``--tag`` check in build-tauri.yml is what prevents that.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (path, regex with one group around the version). Line-oriented edits, not a
# parse-and-dump, so comments and key order in each file survive.
MANIFESTS: tuple[tuple[str, str], ...] = (
    ("engine/pyproject.toml", r'^version = "([^"]+)"$'),
    ("engine/wrenote/__init__.py", r'^__version__ = "([^"]+)"$'),
    ("shells/tauri/src-tauri/tauri.conf.json", r'^  "version": "([^"]+)",$'),
    ("shells/tauri/src-tauri/Cargo.toml", r'^version = "([^"]+)"$'),
    ("shells/tauri/package.json", r'^  "version": "([^"]+)",$'),
    ("shells/electron/package.json", r'^  "version": "([^"]+)",$'),
)
# Cargo rewrites this itself on the next build; set it too so a `set` leaves a
# clean tree, but it is not a source of truth and is not checked.
CARGO_LOCK = "shells/tauri/src-tauri/Cargo.lock"

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def read_all(root: Path = ROOT) -> dict[str, str]:
    found: dict[str, str] = {}
    for rel, pattern in MANIFESTS:
        text = (root / rel).read_text(encoding="utf-8")
        m = re.search(pattern, text, re.M)
        if not m:
            raise SystemExit(f"{rel}: no version line matching {pattern!r}")
        found[rel] = m.group(1)
    return found


def check(root: Path = ROOT, tag: str | None = None) -> str:
    """The agreed version, or a SystemExit naming the disagreement."""
    found = read_all(root)
    versions = sorted(set(found.values()))
    if len(versions) != 1:
        lines = "\n".join(f"  {v:10s} {rel}" for rel, v in found.items())
        raise SystemExit(f"manifests disagree on the version:\n{lines}\n"
                         f"run: python packaging/release/version.py set <version>")
    version = versions[0]
    if tag is not None:
        want = tag[1:] if tag.startswith("v") else tag
        if want != version:
            raise SystemExit(f"tag {tag} but the manifests say {version}")
    return version


def set_version(version: str, root: Path = ROOT) -> list[str]:
    if not SEMVER.match(version):
        raise SystemExit(f"{version!r} is not MAJOR.MINOR.PATCH[-prerelease]")
    changed: list[str] = []
    for rel, pattern in MANIFESTS:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new, n = re.subn(
            pattern,
            lambda m: m.group(0).replace(m.group(1), version),
            text, count=1, flags=re.M,
        )
        if n != 1:
            raise SystemExit(f"{rel}: no version line matching {pattern!r}")
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(rel)
    lock = root / CARGO_LOCK
    if lock.exists():
        text = lock.read_text(encoding="utf-8")
        new = re.sub(
            r'(name = "wrenote-shell"\nversion = ")[^"]+(")',
            lambda m: f"{m.group(1)}{version}{m.group(2)}",
            text, count=1,
        )
        if new != text:
            lock.write_text(new, encoding="utf-8")
            changed.append(CARGO_LOCK)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="version.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail unless every manifest agrees")
    parser.add_argument("--tag", help="with --check: the git tag must also match (v-prefix optional)")
    sub = parser.add_subparsers(dest="cmd")
    p_set = sub.add_parser("set", help="write VERSION into every manifest")
    p_set.add_argument("version")
    args = parser.parse_args(argv)

    if args.cmd == "set":
        for rel in set_version(args.version):
            print(f"updated {rel}")
        return 0
    version = check(tag=args.tag)
    print(version if not args.check else f"version {version} agreed across {len(MANIFESTS)} manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
