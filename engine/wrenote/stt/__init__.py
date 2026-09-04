"""STT subpackage. Importing here triggers backend registrations."""
from . import (
    mock,  # noqa: F401  -- registers `mock`
    whisper_cpp,  # noqa: F401  -- registers `whisper_cpp`
)
