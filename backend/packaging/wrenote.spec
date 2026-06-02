# -*- mode: python ; coding: utf-8 -*-
"""Cross-platform PyInstaller spec for the Wrenote desktop app.

  macOS    -> Wrenote.app  (onedir BUNDLE; system audio via the `syscap` helper)
  Windows  -> Wrenote/ folder + Wrenote.exe  (WebView2)

Bundles the server + native STT (pywhispercpp) + llama.cpp + ONNX VAD + the
built SPA + ffmpeg. Excludes torch/speechbrain — offline speaker diarization
uses the ONNX model downloaded at runtime. Run from backend/:
    pyinstaller packaging/wrenote.spec
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
    """Resolve an ffmpeg binary and copy it to a standard name so the app can
    invoke it as plain `ffmpeg(.exe)`. Prefers PATH (GitHub runners ship it),
    then imageio-ffmpeg, then the local conda env."""
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

datas = [
    (os.path.join(BACKEND, "static", "app"), "static/app"),
    (os.path.join(BACKEND, "config.yaml"), "."),
    (os.path.join(BACKEND, "wrenote", "vad", "assets", "silero_vad.onnx"), "wrenote/vad/assets"),
]
datas += collect_data_files("pywhispercpp")
datas += collect_data_files("llama_cpp")
datas += collect_data_files("webview")  # WebView2 loader (Windows) / mac webview assets

hiddenimports = []
hiddenimports += collect_submodules("wrenote")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview")
hiddenimports += ["wrenote.server"]
if IS_WIN:
    hiddenimports += ["clr"]  # pythonnet, for pywebview's edgechromium (WebView2) backend

excludes = [
    # torch/speechbrain are only for OFFLINE speaker diarization (ONNX model
    # downloaded at runtime), not recording — keep them out (saves ~2 GB).
    "torch", "torchaudio", "torchgen", "speechbrain", "silero_vad",
    "tkinter", "matplotlib", "PIL", "pandas", "IPython", "notebook", "pytest",
]

a = Analysis(
    [os.path.join(BACKEND, "run_desktop.py")],
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
    name="Wrenote",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Wrenote")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Wrenote.app",
        icon=None,
        bundle_identifier="com.wrenote.app",
        info_plist={
            "CFBundleName": "Wrenote",
            "CFBundleDisplayName": "Wrenote",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Wrenote transcribes speech from your microphone, locally on your device."
            ),
        },
    )
