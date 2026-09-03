#!/usr/bin/env python3
"""Patch an extracted pywhispercpp sdist so it builds as a Wrenote runtime pack.

pywhispercpp's ``setup.py`` (1.4.x) has two assumptions that break our
accelerated Windows builds:

1. It looks for the freshly built DLLs under ``bin/<Config>/``, which only
   multi-config generators (Visual Studio) produce. With Ninja they land in
   ``bin/`` directly, and the wheel repair step then can't find whisper.dll.
2. It repairs the wheel with ``repairwheel`` (→ delvewheel), which vendors
   every DLL the extension depends on and aborts when one is missing. On a
   GPU-less runner ``nvcuda.dll`` (the NVIDIA driver) doesn't exist, and we
   don't want the CUDA runtime or ``vulkan-1.dll`` vendored either: the pack
   ships the CUDA runtime once in ``bin/`` for both llama.cpp and whisper.cpp,
   and the Vulkan loader belongs to the user's driver.

So: fall back to ``bin/`` when ``bin/<Config>`` is absent, and call delvewheel
directly with ``--exclude`` taken from ``WRENOTE_DELVEWHEEL_EXCLUDE``
(':'-delimited). Name-mangling stays on — whisper.cpp's ggml DLLs must not
collide with llama_cpp's copies in the same process.

Usage: patch_pywhispercpp.py <extracted-sdist-dir>
"""
from __future__ import annotations

import sys
from pathlib import Path


def patch(setup_py: Path) -> None:
    s = setup_py.read_text(encoding="utf-8")

    old_dll = "        dll_folder = os.path.join(self.build_temp, '_pywhispercpp', 'bin', cfg)\n"
    new_dll = (
        "        dll_folder = os.path.join(self.build_temp, '_pywhispercpp', 'bin', cfg)\n"
        "        if not os.path.isdir(dll_folder):\n"
        "            # single-config generators (Ninja) don't nest the output by config\n"
        "            dll_folder = os.path.join(self.build_temp, '_pywhispercpp', 'bin')\n"
    )
    if old_dll not in s:
        raise SystemExit("patch_pywhispercpp: dll_folder line not found; setup.py changed upstream")
    s = s.replace(old_dll, new_dll)

    old_repair = "            subprocess.call(['repairwheel', wheel_path, '-o', tmp_dir, '-l', dll_folder])\n"
    new_repair = (
        "            cmd = [sys.executable, '-m', 'delvewheel', 'repair', str(wheel_path),\n"
        "                   '-w', str(tmp_dir), '--add-path', dll_folder]\n"
        "            excl = os.environ.get('WRENOTE_DELVEWHEEL_EXCLUDE', '').strip(':')\n"
        "            if excl:\n"
        "                cmd += ['--exclude', excl]\n"
        "            print('wrenote: repairing with', ' '.join(cmd))\n"
        "            subprocess.check_call(cmd)\n"
    )
    if old_repair not in s:
        raise SystemExit("patch_pywhispercpp: repairwheel call not found; setup.py changed upstream")
    s = s.replace(old_repair, new_repair)

    if "\nimport sys\n" not in s:
        s = s.replace("\nimport os\n", "\nimport os\nimport sys\n", 1)
    setup_py.write_text(s, encoding="utf-8")
    print(f"patched {setup_py}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    patch(Path(argv[1]) / "setup.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
