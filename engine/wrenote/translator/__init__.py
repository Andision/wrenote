"""Translator subpackage. Importing here triggers backend registrations."""
from . import (
    disabled,  # noqa: F401  -- registers `disabled`
    llama_cpp,  # noqa: F401  -- registers `llama_cpp`
    llama_server,  # noqa: F401  -- registers `llama_server`
    mock,  # noqa: F401  -- registers `mock`
    openai_compatible,  # noqa: F401  -- registers `openai_compatible`
)
