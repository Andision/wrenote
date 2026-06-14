# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Wrenote **server sidecar** (Electron shell).

Unlike wrenote.spec (the legacy pywebview app that BUNDLEs Wrenote.app), this
freezes the pure FastAPI server with NO window / pywebview — Electron owns the
window and spawns this as a sidecar over loopback HTTP/WS. Output:

    dist/wrenote-server/   (onedir: `wrenote-server` exe + _internal + helpers)

Electron bundles that folder via electron-builder `extraResources`. Run from backend/:
    pyinstaller packaging/wrenote_server.spec
"""
import os
import shutil
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
BACKEND = os.path.dirname(SPECPATH)  # noqa: F821 — SPECPATH injected by PyInstaller
CACHE = os.path.join(BACKEND, "packaging", ".build_cache")


def _bundled_ffmpeg() -> str | None:
    """Resolve an ffmpeg binary and copy it to a standard name (same logic as
    wrenote.spec). PATH first, then imageio-ffmpeg, then the local conda env."""
    src = shutil.which("ffmpeg")
    if not src:
        try:
            import imageio_ffmpeg

            src = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            src = None
    if not src:
        conda = os.path.expanduser("~/miniforge3/envs/wrenote/bin/ffmpeg")
        src = conda if os.path.exists(conda) else None
    if not src:
        return None
    os.makedirs(CACHE, exist_ok=True)
    dst = os.path.join(CACHE, "ffmpeg.exe" if IS_WIN else "ffmpeg")
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    return dst


binaries = []
binaries += collect_dynamic_libs("pywhispercpp")
binaries += collect_dynamic_libs("llama_cpp")
binaries += collect_dynamic_libs("onnxruntime")

ffmpeg = _bundled_ffmpeg()
if ffmpeg:
    binaries += [(ffmpeg, ".")]  # next to the executable; launcher adds that dir to PATH

if IS_MAC:
    syscap = os.path.join(BACKEND, "packaging", "macos", "syscap")
    if os.path.exists(syscap):
        binaries += [(syscap, ".")]  # ScreenCaptureKit system-audio helper
    screencap = os.path.join(BACKEND, "packaging", "macos", "screencap")
    if os.path.exists(screencap):
        binaries += [(screencap, ".")]  # ScreenCaptureKit window/display video helper

datas = [
    (os.path.join(BACKEND, "static", "app"), "static/app"),
    (os.path.join(BACKEND, "config.yaml"), "."),
    (os.path.join(BACKEND, "wrenote", "vad", "assets", "silero_vad.onnx"), "wrenote/vad/assets"),
]
datas += collect_data_files("pywhispercpp")
datas += collect_data_files("llama_cpp")

# No pywebview: Electron is the shell. Drop wrenote.desktop (it imports webview at
# module top level) from the wrenote submodule sweep so the frozen server never
# pulls in pywebview, and exclude `webview` outright.
hiddenimports = [m for m in collect_submodules("wrenote") if m != "wrenote.desktop"]
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["wrenote.server", "wrenote.run_server"]
if IS_WIN:
    hiddenimports += collect_submodules("soundcard")  # WASAPI loopback (system audio)

excludes = [
    # torch/speechbrain are only for OFFLINE speaker diarization (ONNX model
    # downloaded at runtime), not recording — keep them out (saves ~2 GB).
    "torch", "torchaudio", "torchgen", "speechbrain", "silero_vad",
    "tkinter", "matplotlib", "PIL", "pandas", "IPython", "notebook", "pytest",
    "webview",  # pywebview — Electron owns the window now
]

a = Analysis(
    [os.path.join(BACKEND, "server_entry.py")],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wrenote-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Keep a real stdout for the Electron port handshake (`WRENOTE_PORT=`). It's
    # spawned by Electron, so no terminal window ever appears.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
# onedir COLLECT only — NO BUNDLE. Electron is the .app; this folder is embedded
# inside it via electron-builder extraResources.
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="wrenote-server")
