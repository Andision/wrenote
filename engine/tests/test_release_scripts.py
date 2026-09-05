"""packaging/release/: the version lives in six manifests, and latest.json is
what every installed copy reads to learn a release exists.

Held here: `version.py set` touches all six and `--check` catches one left
behind (or a tag that disagrees); `make_latest.py` places each installer
under the updater's platform key from the name Tauri gives it, carries a
signature when one is there, and refuses to publish an empty or ambiguous
index — a stray artifact must never become "the Windows build".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[2] / "packaging" / "release"
sys.path.insert(0, str(RELEASE))
import make_latest  # noqa: E402
import version as version_mod  # noqa: E402

# ---------- version.py ----------

def _repo(tmp_path: Path, version="0.1.0") -> Path:
    files = {
        "engine/pyproject.toml": f'[project]\nname = "wrenote"\nversion = "{version}"\n',
        "engine/wrenote/__init__.py": f'"""doc"""\n__version__ = "{version}"\n',
        "shells/tauri/src-tauri/tauri.conf.json":
            f'{{\n  "productName": "Wrenote",\n  "version": "{version}",\n  "identifier": "x"\n}}\n',
        "shells/tauri/src-tauri/Cargo.toml": f'[package]\nname = "wrenote-shell"\nversion = "{version}"\n',
        "shells/tauri/package.json": f'{{\n  "name": "shell",\n  "version": "{version}",\n  "private": true\n}}\n',
        "shells/electron/package.json": f'{{\n  "name": "desktop",\n  "version": "{version}",\n  "main": "main.js"\n}}\n',
        "shells/tauri/src-tauri/Cargo.lock":
            f'[[package]]\nname = "serde"\nversion = "1.0.0"\n\n[[package]]\nname = "wrenote-shell"\nversion = "{version}"\n',
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_real_manifests_agree():
    """The repo itself passes the check CI runs."""
    assert version_mod.check() == version_mod.read_all()["engine/pyproject.toml"]


def test_set_rewrites_every_manifest_and_the_lock(tmp_path):
    root = _repo(tmp_path)
    changed = version_mod.set_version("0.2.0", root)
    assert set(changed) == {rel for rel, _ in version_mod.MANIFESTS} | {version_mod.CARGO_LOCK}
    assert version_mod.check(root) == "0.2.0"
    # Line-oriented: the rest of each file is untouched.
    assert '"productName": "Wrenote"' in (root / "shells/tauri/src-tauri/tauri.conf.json").read_text()
    lock = (root / version_mod.CARGO_LOCK).read_text()
    assert 'name = "serde"\nversion = "1.0.0"' in lock and 'name = "wrenote-shell"\nversion = "0.2.0"' in lock


def test_one_manifest_left_behind_fails_the_check(tmp_path):
    root = _repo(tmp_path)
    version_mod.set_version("0.2.0", root)
    (root / "shells/electron/package.json").write_text(
        '{\n  "name": "desktop",\n  "version": "0.1.0",\n  "main": "main.js"\n}\n'
    )
    with pytest.raises(SystemExit, match="disagree"):
        version_mod.check(root)


def test_the_tag_must_match(tmp_path):
    root = _repo(tmp_path, "0.2.0")
    assert version_mod.check(root, tag="v0.2.0") == "0.2.0"
    assert version_mod.check(root, tag="0.2.0") == "0.2.0"
    with pytest.raises(SystemExit, match=r"tag v0\.3\.0"):
        version_mod.check(root, tag="v0.3.0")


def test_only_semver_is_accepted(tmp_path):
    root = _repo(tmp_path)
    for bad in ("0.2", "v0.2.0", "latest", "0.2.0.1"):
        with pytest.raises(SystemExit):
            version_mod.set_version(bad, root)
    version_mod.set_version("0.2.0-beta.1", root)
    assert version_mod.check(root) == "0.2.0-beta.1"


# ---------- make_latest.py ----------

@pytest.mark.parametrize("name,key", [
    ("Wrenote_0.2.0_aarch64.dmg", "darwin-aarch64"),
    ("Wrenote_0.2.0_x64.dmg", "darwin-x86_64"),
    ("Wrenote_0.2.0_x64-setup.exe", "windows-x86_64"),
    ("Wrenote_0.2.0_arm64-setup.exe", "windows-aarch64"),
    ("Wrenote_0.2.0_x64_en-US.msi", "windows-x86_64"),
    ("Wrenote_0.2.0_amd64.AppImage", "linux-x86_64"),
    ("latest.json", None),
    ("Wrenote.app", None),
    ("Wrenote_0.2.0.dmg", None),  # no arch: don't guess
])
def test_platform_key_from_tauris_file_names(name, key):
    assert make_latest.platform_key(name) == key


def test_index_places_each_installer_and_carries_a_signature(tmp_path):
    for name in ("Wrenote_0.2.0_aarch64.dmg", "Wrenote_0.2.0_x64-setup.exe", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "Wrenote_0.2.0_x64-setup.exe.sig").write_text("minisign-sig\n")
    index = make_latest.make_latest(
        [p for p in tmp_path.iterdir()], version="0.2.0", tag="v0.2.0",
        repo="Andision/wrenote", notes="Faster.", pub_date="2026-09-05T00:00:00Z",
    )
    assert index == {
        "version": "0.2.0",
        "notes": "Faster.",
        "pub_date": "2026-09-05T00:00:00Z",
        "release_url": "https://github.com/Andision/wrenote/releases/tag/v0.2.0",
        "platforms": {
            "darwin-aarch64": {
                "url": "https://github.com/Andision/wrenote/releases/download/v0.2.0/Wrenote_0.2.0_aarch64.dmg",
                "signature": "",
            },
            "windows-x86_64": {
                "url": "https://github.com/Andision/wrenote/releases/download/v0.2.0/Wrenote_0.2.0_x64-setup.exe",
                "signature": "minisign-sig",
            },
        },
    }


def test_the_engine_reads_what_make_latest_writes(tmp_path):
    """The two ends of the channel agree on the format."""
    from wrenote.core.config import UpdateConfig
    from wrenote.core.update import UpdateChecker

    (tmp_path / "Wrenote_0.2.0_x64-setup.exe").write_bytes(b"x")
    index = make_latest.make_latest(
        [tmp_path / "Wrenote_0.2.0_x64-setup.exe"], version="0.2.0", tag="v0.2.0", repo="o/r",
    )
    checker = UpdateChecker(
        UpdateConfig(index_url="https://github.com/o/r/releases/latest/download/latest.json"),
        current="0.1.0", platform_key="windows-x86_64",
        fetch=lambda _url: json.dumps(index).encode(),
    )
    s = checker.status()
    assert s["available"] and s["download_url"].endswith("_x64-setup.exe")
    assert s["release_url"] == "https://github.com/o/r/releases/tag/v0.2.0"


def test_two_installers_for_one_platform_is_an_error(tmp_path):
    for name in ("Wrenote_0.2.0_x64-setup.exe", "Wrenote_0.2.0_x64_en-US.msi"):
        (tmp_path / name).write_bytes(b"x")
    with pytest.raises(SystemExit, match="two installers"):
        make_latest.make_latest(list(tmp_path.iterdir()), version="0.2.0", tag="v0.2.0", repo="o/r")


def test_no_installers_is_an_error(tmp_path):
    (tmp_path / "README.md").write_text("no builds here")
    with pytest.raises(SystemExit, match="no installers"):
        make_latest.make_latest(list(tmp_path.iterdir()), version="0.2.0", tag="v0.2.0", repo="o/r")


def test_cli_refuses_a_tag_that_disagrees_with_the_version(tmp_path):
    (tmp_path / "Wrenote_0.2.0_aarch64.dmg").write_bytes(b"x")
    with pytest.raises(SystemExit, match="does not match"):
        make_latest.main(["--version", "0.2.0", "--tag", "v0.3.0", "--repo", "o/r", "--dir", str(tmp_path)])
    out = tmp_path / "latest.json"
    assert make_latest.main([
        "--version", "0.2.0", "--tag", "v0.2.0", "--repo", "o/r", "--dir", str(tmp_path), "--out", str(out),
    ]) == 0
    assert json.loads(out.read_text())["platforms"].keys() == {"darwin-aarch64"}
