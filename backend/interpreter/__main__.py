"""Run the server with `python -m interpreter`."""
from __future__ import annotations

import uvicorn

from .core.config import load_config


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "interpreter.server:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
