"""The webhook endpoint: verify, persist, answer. Nothing slow happens before
the answer; the worker in bridge_sync.py applies queued events afterwards."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .verify import verify

MAX_BODY = 256 * 1024  # deliveries are a few hundred bytes


def make_server(host: str, port: int, path: str, secret: str, store, on_queued=lambda: None,
                log=lambda line: None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "isbridgeup-example"
        sys_version = ""

        def do_POST(self):
            if self.path.split("?", 1)[0] != path:
                return self._reply(404)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411)
            if length < 0 or length > MAX_BODY:
                return self._reply(413)
            raw = self.rfile.read(length)  # the exact bytes we signed
            if not verify(secret, raw, self.headers.get("X-IsBridgeUp-Signature")):
                log("rejected a delivery: bad, stale or missing signature")
                return self._reply(401)  # a real delivery is retried
            try:
                event = json.loads(raw)
                event_id, event_type = str(event["id"]), event["type"]
            except (ValueError, KeyError, TypeError):
                return self._reply(400)
            fresh = store.enqueue(event_id, raw.decode("utf-8"))
            self._reply(204)  # persisted, so it is safe to acknowledge now
            log(f"{'queued' if fresh else 'already queued'} {event_type} {event_id} "
                f"(delivery {self.headers.get('X-IsBridgeUp-Delivery', '-')})")
            on_queued()

        def do_GET(self):
            if self.path == "/healthz":
                return self._reply(200, b"ok\n")
            self._reply(404)

        def _reply(self, status: int, body: bytes = b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # the default access log is noisy; we log what matters above

    return ThreadingHTTPServer((host, port), Handler)
