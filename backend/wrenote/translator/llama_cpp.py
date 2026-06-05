"""llama.cpp translator backend (Hy-MT2 default).

Per design.v1.1 §3.3 / §4.2.3. Uses llama-cpp-python's
``create_chat_completion`` so the model's own chat template (embedded in
the GGUF) handles prompt formatting. Tested with Tencent's Hy-MT2-1.8B.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
from typing import Any

from ..core.events import BackendInfo
from ..core.registry import register_translator
from .base import TranslatorBackend

log = logging.getLogger(__name__)


_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
}


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get(code.lower(), code)


@register_translator("llama_cpp")
class LlamaCppTranslator(TranslatorBackend):
    def __init__(
        self,
        *,
        model_path: str,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        temperature: float = 0.0,
        max_tokens: int = 512,
        n_threads: int | None = None,
    ) -> None:
        self._model_path = str(Path(model_path).expanduser().resolve())
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self._llm: Any = None
        # Optional glossary instruction (core.glossary), injected into the prompt.
        self._glossary_text: str = ""
        # Same reasoning as WhisperCppBackend: llama.cpp + Metal isn't
        # multi-thread safe. A single dedicated worker serialises all C calls
        # (partial-translation loop + final-translation loop both submit here).
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="llama"
        )

    async def load(self) -> None:
        if self._llm is not None:
            return
        path = Path(self._model_path)
        if not path.exists():
            raise FileNotFoundError(f"Translator model not found at {self._model_path}")

        def _load() -> Any:
            from llama_cpp import Llama

            return Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_gpu_layers=self._n_gpu_layers,
                n_threads=self._n_threads,
                verbose=False,
            )

        log.info(
            "Loading llama.cpp translator from %s (n_gpu_layers=%d, n_ctx=%d)",
            self._model_path,
            self._n_gpu_layers,
            self._n_ctx,
        )
        loop = asyncio.get_event_loop()
        self._llm = await loop.run_in_executor(self._executor, _load)
        log.info("llama.cpp translator loaded")

    async def unload(self) -> None:
        def _drop() -> None:
            self._llm = None
        if self._llm is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(self._executor, _drop)
            except RuntimeError:
                self._llm = None
        self._executor.shutdown(wait=False)

    async def translate(
        self,
        text: str,
        *,
        src: str,
        tgt: str,
        timeout_s: float = 10.0,
    ) -> str:
        if self._llm is None:
            raise RuntimeError("LlamaCppTranslator not loaded; call load() first")
        text = text.strip()
        if not text:
            return ""

        prompt = self._build_prompt(text, src=src, tgt=tgt)

        def _generate() -> str:
            resp = self._llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            return resp["choices"][0]["message"]["content"].strip()

        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, _generate),
            timeout=timeout_s,
        )

    def _build_prompt(self, text: str, *, src: str, tgt: str) -> str:
        src_name = _lang_name(src)
        tgt_name = _lang_name(tgt)
        glossary = f"{self._glossary_text} " if self._glossary_text else ""
        return (
            f"Translate the following {src_name} text into {tgt_name}. "
            f"Output only the translation, no explanation. {glossary}\n\n{text}"
        )

    def set_glossary(self, pairs: list[tuple[str, str]]) -> None:
        from ..core.glossary import mt_glossary_text

        self._glossary_text = mt_glossary_text(pairs)

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            name="llama_cpp_translator",
            version="llama-cpp-python-0.3.23",
            model=Path(self._model_path).stem,
            device="metal-or-cuda",
            supported_languages=list(_LANG_NAMES),
            capabilities={
                "n_ctx": self._n_ctx,
                "n_gpu_layers": self._n_gpu_layers,
                "temperature": self._temperature,
            },
        )
