"""VAD subpackage. Importing here triggers backend registrations."""
from . import (
    disabled,  # noqa: F401  -- registers `disabled`
    silero,  # noqa: F401  -- registers `silero`
)
