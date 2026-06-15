"""PyInstaller entry point for the desktop app — see packaging/wrenote.spec.

A module-level ``-m`` invocation isn't available in a frozen app, so freezing
targets this thin script which just calls the launcher.
"""
from wrenote.desktop import main

if __name__ == "__main__":
    main()
