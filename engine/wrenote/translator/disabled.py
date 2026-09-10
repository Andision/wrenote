"""No-op translator, for a session that translates nothing.

Two different things end up here. A session the user started with the
translate toggle off, and translation switched off as a *feature*
(``translator.enabled``), where the model was never downloaded — so the real
backend cannot even be constructed, since it takes a ``model_path``.

:class:`~wrenote.core.pipeline.Pipeline` never loads or calls a translator
when ``translate_enabled`` is false, but it does hold one and name it in its
start-up log. This is that object, and it raises rather than inventing text
if some path ever does ask it to translate.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..core.events import BackendInfo
from ..core.registry import register_translator
from .base import TranslatorBackend


@register_translator("disabled")
class DisabledTranslatorBackend(TranslatorBackend):
    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
        context: Sequence[str] = (),
    ) -> str:
        raise RuntimeError("translation is switched off for this session")

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="disabled_translator", version="0.1", model="none", device="cpu",
        )
