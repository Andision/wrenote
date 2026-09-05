"""Backend registry: name → class, with factory helpers.

Per design.v1.1 §4.3. Backends register themselves via decorators when their
modules are imported; the package __init__.py files trigger those imports.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from ..chat.base import ChatBackend
    from ..speaker.base import SpeakerBackend
    from ..stt.base import STTBackend
    from ..translator.base import TranslatorBackend
    from ..vad.base import VADBackend


T = TypeVar("T")


STT_REGISTRY: dict[str, type] = {}
VAD_REGISTRY: dict[str, type] = {}
TRANSLATOR_REGISTRY: dict[str, type] = {}
SPEAKER_REGISTRY: dict[str, type] = {}
CHAT_REGISTRY: dict[str, type] = {}


def _register(registry: dict[str, type], name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        if name in registry:
            raise ValueError(f"Backend {name!r} already registered (was {registry[name]!r})")
        registry[name] = cls
        return cls
    return decorator


def register_stt(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register an STT backend class under `name`."""
    return _register(STT_REGISTRY, name)


def register_vad(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register a VAD backend class under `name`."""
    return _register(VAD_REGISTRY, name)


def register_translator(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register a translator backend class under `name`."""
    return _register(TRANSLATOR_REGISTRY, name)


def register_speaker(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register a speaker-embedding backend class under `name`."""
    return _register(SPEAKER_REGISTRY, name)


def register_chat(name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register a chat backend class under `name`."""
    return _register(CHAT_REGISTRY, name)


def _make(registry: dict[str, type], kind: str, backend: str, params: dict[str, Any]) -> Any:
    if backend not in registry:
        raise ValueError(
            f"Unknown {kind} backend: {backend!r}; available: {sorted(registry)}"
        )
    return registry[backend](**params)


def make_stt(backend: str, params: dict[str, Any] | None = None) -> STTBackend:
    return _make(STT_REGISTRY, "STT", backend, params or {})


def make_vad(backend: str, params: dict[str, Any] | None = None) -> VADBackend:
    return _make(VAD_REGISTRY, "VAD", backend, params or {})


def make_translator(backend: str, params: dict[str, Any] | None = None) -> TranslatorBackend:
    return _make(TRANSLATOR_REGISTRY, "translator", backend, params or {})


def make_speaker(backend: str, params: dict[str, Any] | None = None) -> SpeakerBackend:
    return _make(SPEAKER_REGISTRY, "speaker", backend, params or {})


def make_chat(backend: str, params: dict[str, Any] | None = None) -> ChatBackend:
    return _make(CHAT_REGISTRY, "chat", backend, params or {})
