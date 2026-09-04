"""Chat subpackage. Importing here triggers backend registrations."""
from . import (
    llama_cpp,  # noqa: F401  -- registers `llama_cpp`
    mock,  # noqa: F401  -- registers `mock`
)
