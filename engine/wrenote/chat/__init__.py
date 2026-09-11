"""Chat subpackage. Importing here triggers backend registrations."""
from . import (
    llama_server,  # noqa: F401  -- registers `llama_server`
    mock,  # noqa: F401  -- registers `mock`
    openai_compatible,  # noqa: F401  -- registers `openai_compatible`
)
