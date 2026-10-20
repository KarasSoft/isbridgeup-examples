"""bridge_sync.py as a process: a local stand-in for the API, real signed
deliveries to its receiver, and a clean stop."""

import json
import os
import pathlib
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from send_event import sign

from .helpers import SECRET, fixture

HERE = pathlib.Path(__file__).resolve().parents[1]


class FakeApi(BaseHTTPRequestHandler):
    live_reads = 0

    def do_GET(self):
        if self.path == "/v1/bridges":
            body = fixture("catalog.json")
        elif self.path == "/v1/bridges/live":
            FakeApi.live_reads += 1
            body = fixture("live.json")
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
                   IBU_KEY="ibu_live_example", IBU_API_BASE=f"http://127.0.0.1:{self.api.server_address[1]}",
                   IBU_DB=os.path.join(self.tmp.name, "sync.sqlite3"), IBU_WEBHOOK_SECRET=SECRET,
                   IBU_PORT=str(self.port), IBU_POLL_SECONDS="300")
        self.proc = subprocess.Popen([sys.executable, "bridge_sync.py"], cwd=HERE, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(line) for line in self.proc.stdout], daemon=True).start()

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)
        self.proc.stdout.close()
        self.api.shutdown()
        self.api.server_close()
        self.tmp.cleanup()

    def wait_for(self, text, timeout=10.0):
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            seen.append(line)
            if text in line:
                return line
        self.fail(f"never printed {text!r}; got:\n{''.join(seen)}")

    def deliver(self, name):
        body = json.dumps(fixture(name), separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/webhooks/isbridgeup", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "X-IsBridgeUp-Signature": sign(SECRET, body, int(time.time()))})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def test_snapshot_webhooks_reread_and_stop(self):
        self.wait_for("BLOCKED  Brickell loop")
        self.wait_for("receiving webhooks")
        reads = FakeApi.live_reads

        self.assertEqual(self.deliver("events/brickell-lowered.json"), 204)
        self.wait_for("CLEAR    Brickell loop")
        self.assertEqual(FakeApi.live_reads, reads)            # a plain change needs no re-read

        self.assertEqual(self.deliver("events/outage-degraded.json"), 204)
        deadline = time.monotonic() + 10
        while FakeApi.live_reads == reads and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(FakeApi.live_reads, reads)          # every outage event: read again

        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(10), 0)


if __name__ == "__main__":
    unittest.main()
