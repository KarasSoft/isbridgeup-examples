"""The receiver over real HTTP: verify, persist, answer; the worker applies once."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from ibu.receiver import MAX_BODY, make_server
from ibu.store import Store
from ibu.sync import Sync
from send_event import sign

from .helpers import BRICKELL, ROUTES, SECRET, fixture

PATH = "/webhooks/isbridgeup"


class Receiver(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.sync = Sync(ROUTES, self.store)
        self.sync.apply_snapshot(fixture("live.json"), set_clock=False)
        self.queued = threading.Event()
        self.server = make_server("127.0.0.1", 0, PATH, SECRET, self.store, on_queued=self.queued.set)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, body: bytes, signature=None, path=PATH, length=None):
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-IsBridgeUp-Signature"] = signature
        request = urllib.request.Request(self.url + path, data=body, method="POST", headers=headers)
        if length is not None:
            request.add_unredirected_header("Content-Length", str(length))
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_a_signed_delivery_is_queued_then_applied_once(self):
        event = fixture("events/brickell-lowered.json")
        body = json.dumps(event, separators=(",", ":")).encode()
        self.assertEqual(self.post(body, sign(SECRET, body, int(time.time()))), 204)
        self.assertTrue(self.queued.wait(2))
        self.assertEqual(self.post(body, sign(SECRET, body, int(time.time()))), 204)   # a retry
        self.assertEqual(self.store.queued(), 1)
        self.assertFalse(self.sync.drain_inbox())
        self.assertEqual(self.sync.states.bridges[BRICKELL]["state"], "CLEAR")
        self.assertEqual(self.post(body, sign(SECRET, body, int(time.time()))), 204)   # after it was applied
        self.assertFalse(self.sync.drain_inbox())
        self.assertEqual(self.store.queued(), 0)

    def test_rejected_deliveries_change_nothing(self):
        body = json.dumps(fixture("events/brickell-lowered.json")).encode()
        now = int(time.time())
        self.assertEqual(self.post(body, sign("whsec_wrong", body, now)), 401)
        self.assertEqual(self.post(body, sign(SECRET, body, now - 3600)), 401)
        self.assertEqual(self.post(body), 401)
        self.assertEqual(self.post(body, sign(SECRET, body, now), path="/other"), 404)
        not_json = b"not json"
        self.assertEqual(self.post(not_json, sign(SECRET, not_json, now)), 400)
        no_id = b'{"type": "status.changed"}'
        self.assertEqual(self.post(no_id, sign(SECRET, no_id, now)), 400)
        self.assertEqual(self.post(body, sign(SECRET, body, now), length=MAX_BODY + 1), 413)
        self.assertEqual(self.store.queued(), 0)

    def test_health(self):
        with urllib.request.urlopen(self.url + "/healthz", timeout=5) as response:
            self.assertEqual(response.read(), b"ok\n")


if __name__ == "__main__":
    unittest.main()
