"""VAD subpackage. Importing here triggers backend registrations."""
from . import disabled  # noqa: F401  -- registers `disabled`
from . import silero  # noqa: F401  -- registers `silero`
