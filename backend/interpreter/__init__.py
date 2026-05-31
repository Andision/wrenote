"""Interpreter: local real-time speech transcription and translation."""
# Eager-import subpackages so backend registrations happen on `import interpreter`.
from . import chat, core, speaker, stt, translator, vad  # noqa: F401

__version__ = "0.1.0"
