# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Wrenote desktop app (macOS .app / onedir).

Incremental "smoke" build: bundles the server + native STT (pywhispercpp) +
ONNX VAD + the SPA + ffmpeg, but EXCLUDES the heavy lazy deps (torch /
speechbrain / llama-cpp-python). That keeps the first build small and fast so we
can validate the window + microphone + packaging path before adding them back.
Run from the backend/ dir:  pyinstaller packaging/wrenote.spec
"""
import os

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

BACKEND = os.path.dirname(SPECPATH)  # noqa: F821 — SPECPATH injected by PyInstaller
FFMPEG = os.path.expanduser("~/miniforge3/envs/wrenote/bin/ffmpeg")

binaries = []
binaries += collect_dynamic_libs("pywhispercpp")  # libwhisper + libggml*(+metal)
binaries += collect_dynamic_libs("llama_cpp")  # libllama + its own libggml*(+metal)
binaries += collect_dynamic_libs("onnxruntime")
if os.path.exists(FFMPEG):
    binaries += [(FFMPEG, ".")]  # next to the executable; launcher puts it on PATH

datas = [
    (os.path.join(BACKEND, "static", "app"), "static/app"),
    (os.path.join(BACKEND, "config.yaml"), "."),
    (os.path.join(BACKEND, "wrenote", "vad", "assets", "silero_vad.onnx"), "wrenote/vad/assets"),
]
datas += collect_data_files("pywhispercpp")
datas += collect_data_files("llama_cpp")

hiddenimports = []
hiddenimports += collect_submodules("wrenote")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["wrenote.server"]

excludes = [
    # torch/speechbrain are only for OFFLINE speaker diarization, not recording,
    # so they stay out of this build (saves ~2 GB). llama_cpp IS needed (the
    # translator/chat backends) so it is bundled.
    "torch", "torchaudio", "torchgen", "speechbrain", "silero_vad",
    # Stuff those would otherwise drag in.
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Wrenote",
)
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
