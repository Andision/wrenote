#!/usr/bin/env python3
"""Verify/update catalogue hashes from Hugging Face, preserving YAML comments.

LFS metadata supplies a content SHA-256. Ordinary Git files (e.g. tokens.txt)
must be downloaded and hashed: their blobId is NOT a content SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://huggingface.co"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def file_metadata(repo: str, revision: str, row: dict) -> tuple[int, str]:
    lfs = row.get("lfs")
    if lfs:
        digest = lfs["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid LFS SHA-256: {repo}/{row['rfilename']}")
        return int(lfs["size"]), digest
    # Pin to the metadata revision so an upstream push cannot mix two versions.
    url = f"{BASE}/{quote(repo, safe='/')}/resolve/{revision}/{quote(row['rfilename'], safe='/')}"
    data = fetch(url)
    if len(data) != row["size"]:
        raise ValueError(f"Unexpected file size: {repo}/{row['rfilename']}")
    return len(data), hashlib.sha256(data).hexdigest()


def refresh(path: Path, *, check: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    # YAML nodes retain source marks; edit only the scalar values, not comments.
    root = yaml.compose(text)
    def fields(node):
        return {key.value: value for key, value in node.value}

    models = fields(root)["models"].value
    cache = {}
    edits = []
    for model in models:
        for node in fields(model)["files"].value:
            values = fields(node)
            repo = values["repo"].value
            filename = values.get("path", values["filename"]).value
            if repo not in cache:
                doc = json.loads(fetch(f"{BASE}/api/models/{quote(repo, safe='/')}/revision/main?blobs=true"))
                cache[repo] = (doc["sha"], {row["rfilename"]: row for row in doc["siblings"]})
            revision, files = cache[repo]
            size, digest = file_metadata(repo, revision, files[filename])
            for key, actual in (("size", str(size)), ("sha256", digest)):
                scalar = values[key]
                if scalar.value != actual:
                    print(f"{repo}/{filename}: {key} {scalar.value} -> {actual}")
                    edits.append((scalar.start_mark.index, scalar.end_mark.index, actual))
    if not check and edits:
        for start, end, value in sorted(edits, reverse=True):
            text = text[:start] + value + text[end:]
        path.write_text(text, encoding="utf-8")
    return not edits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--catalogue", type=Path, default=ROOT / "engine/models.yaml")
    args = parser.parse_args(argv)
    unchanged = refresh(args.catalogue, check=args.check)
    return 1 if args.check and not unchanged else 0


if __name__ == "__main__":
    raise SystemExit(main())
