"""Speaker subpackage. Importing here triggers backend registrations."""
from . import (
    ecapa,  # noqa: F401  -- registers `ecapa` (default, non-gated)
    pyannote,  # noqa: F401  -- registers `pyannote` (gated, opt-in via config)
)
