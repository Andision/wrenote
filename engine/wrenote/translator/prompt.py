"""The translation prompt, shared by every backend that builds one.

It used to live inside :mod:`wrenote.translator.llama_cpp`, which was fine
while that was the only backend asking a chat model to translate. It isn't:
``openai_compatible`` asks the same question of a model reached over HTTP, and
two copies of a prompt drift the first time one of them is tuned.

The wording is deliberately model-agnostic — Hy-MT reads the ``Context:``
block as a translation-memory hint, a general chat model just follows the
sentence — because "whatever answers" is the point of the HTTP backend.
"""
from __future__ import annotations

from collections.abc import Sequence

#: Codes the translator advertises in ``BackendInfo.supported_languages``.
#: Not a limit on what a model can do — it is what the app names in its UI.
LANG_NAMES: dict[str, str] = {
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


def lang_name(code: str) -> str:
    """The English name of a language code, or the code itself when unknown —
    an unlisted code is still worth passing to the model verbatim."""
    return LANG_NAMES.get(code.lower(), code)


def build_prompt(
    text: str,
    *,
    src: str,
    tgt: str,
    context: Sequence[str] = (),
    glossary_text: str = "",
) -> str:
    """The user turn asking for ``text`` in ``tgt``.

    ``context`` is the source lines spoken just before. They are shown, marked,
    and the instruction names the last block as the only thing to translate:
    a sentence on its own loses what "it", a bare number or a dropped subject
    refer to.
    """
    src_name = lang_name(src)
    tgt_name = lang_name(tgt)
    glossary = f"{glossary_text} " if glossary_text else ""
    prior = [c.strip() for c in context if c and c.strip()]
    if prior:
        context_block = "\n".join(prior)
        return (
            f"Context (the {src_name} lines spoken just before; for reference only, "
            f"do not translate them):\n{context_block}\n\n"
            f"Translate the following {src_name} text into {tgt_name}. "
            f"Output only the translation of this text, no explanation. {glossary}\n\n{text}"
        )
    return (
        f"Translate the following {src_name} text into {tgt_name}. "
        f"Output only the translation, no explanation. {glossary}\n\n{text}"
    )
