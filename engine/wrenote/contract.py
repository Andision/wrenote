"""Export the engine's API contract (OpenAPI) for clients and drift checks.

    python -m wrenote.contract            # writes contract/openapi.json
    python -m wrenote.contract --check    # exit 1 if the committed file is stale

The committed ``contract/openapi.json`` is the interface native clients
(macOS, Windows, …) and the web client are built against; the WebSocket
protocol, which OpenAPI cannot describe, is documented next to it in
``contract/ws-protocol.md``. ``tests/test_contract.py`` fails when the code
and the committed contract disagree, so a route change is always a visible,
reviewed contract change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contract"
OPENAPI_PATH = CONTRACT_DIR / "openapi.json"


def build_openapi() -> dict[str, Any]:
    """The schema of a freshly built app with an all-mock config.

    Route surface doesn't depend on the config, but building with mocks keeps
    this runnable on a machine without the native bindings or models.
    """
    from .core.config import Config
    from .server import create_app

    cfg = Config.model_validate(
        {
            "stt": {"backend": "mock"},
            "stt_offline": {"backend": "mock"},
            "vad": {"backend": "disabled"},
            "translator": {"backend": "mock"},
            "speaker": {"backend": "disabled"},
            "chat": {"backend": "mock"},
        }
    )
    return create_app(cfg).openapi()


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wrenote.contract")
    parser.add_argument("--check", action="store_true", help="verify instead of write")
    parser.add_argument("--out", type=Path, default=OPENAPI_PATH)
    args = parser.parse_args(argv)

    text = render(build_openapi())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date; run `python -m wrenote.contract`", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
