"""STT subpackage. Importing here triggers backend registrations."""
from . import (
    mock,  # noqa: F401  -- registers `mock`
    sherpa_onnx,  # noqa: F401  -- registers `sherpa_onnx`
    whisper_cpp,  # noqa: F401  -- registers `whisper_cpp`
)
