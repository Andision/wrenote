"""A stand-in for `llama-server`, so the supervisor can be tested for real.

Speaks the two things `core/llama_server.py` depends on — a bearer-guarded
`/health` and `/v1/chat/completions` — and takes the same flags. Mocking the
subprocess instead would test the mock: whether the process is actually
spawned, actually waited for, and actually killed is the whole feature.

Failure modes, by environment variable:
  FAKE_LLAMA_EXIT=<code>   exit immediately, as a bad model file makes it
  FAKE_LLAMA_LOADING=<n>   answer /health with 503 n times before 200
  FAKE_LLAMA_NEVER_READY=1 stay at 503 forever
  FAKE_LLAMA_IGNORE_TERM=1 ignore SIGTERM, so the kill path gets exercised
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "served by a llama-server"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--api-key", default="")
    p.add_argument("--ctx-size", type=int, default=0)
    p.add_argument("--n-gpu-layers", type=int, default=0)
    args, _unknown = p.parse_known_args()

    code = os.environ.get("FAKE_LLAMA_EXIT")
    if code:
        print(f"fake llama-server: refusing to load {args.model}", file=sys.stderr)
        return int(code)

    if os.environ.get("FAKE_LLAMA_IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    loading = int(os.environ.get("FAKE_LLAMA_LOADING") or 0)
    never = bool(os.environ.get("FAKE_LLAMA_NEVER_READY"))
    state = {"loading": loading}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            pass

        def _authed(self) -> bool:
            if not args.api_key:
                return True
            return self.headers.get("authorization") == f"Bearer {args.api_key}"

        def _send(self, status: int, body: dict[str, object]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if not self._authed():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path != "/health":
                self._send(404, {"error": "not found"})
                return
            if never or state["loading"] > 0:
                state["loading"] -= 1
                self._send(503, {"status": "loading model"})
                return
            self._send(200, {"status": "ok"})

        def do_POST(self) -> None:
            if not self._authed():
                self._send(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for word in REPLY.split():
                    chunk = {"choices": [{"delta": {"content": word + " "}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": REPLY}}]})

    # Threading, so a health poll that overlaps a completion never queues
    # behind it — the real thing serves concurrently and the stand-in should
    # not introduce a bottleneck the supervisor would then be blamed for.
    server = ThreadingHTTPServer((args.host, args.port), H)
    print(f"fake llama-server listening on {args.host}:{args.port}", file=sys.stderr)
    sys.stderr.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
