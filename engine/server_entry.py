"""PyInstaller entry point for the Electron sidecar server — see
``packaging/wrenote_server.spec``.

This is the Electron-shell counterpart of ``run_desktop.py``: it freezes the
**pure FastAPI server** (no pywebview window — Electron owns the window). A
module-level ``-m`` invocation isn't available in a frozen app, so freezing
targets this thin script which just calls the server launcher.
"""
from wrenote.run_server import main

if __name__ == "__main__":
    main()
