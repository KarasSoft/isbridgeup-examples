"""The REST client against a local stand-in for the API: the key header, the
error shapes, Retry-After."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ibu.client import USER_AGENT, ApiError, Client, error_from

from .helpers import fixture

ANSWERS = {
    "/v1/bridges/live": (200, {}, fixture("live.json")),
    "/v1/bridges": (403, {}, {"detail": {"error": "trial_expired", "message": "Your 7-day trial has ended."}}),
}


class FakeApi(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        FakeApi.seen.append((self.path, self.headers.get("X-API-Key"), self.headers.get("User-Agent")))
        status, headers, body = ANSWERS.get(self.path, (429, {"Retry-After": "17"},
                                                         {"detail": {"error": "rate_limited", "message": "Slow down"}}))
        raw = json.dumps(body).encode()
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = Client("ibu_live_example", f"http://127.0.0.1:{cls.server.server_address[1]}/", timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_live_sends_the_key_and_returns_the_snapshot(self):
        self.assertEqual(self.client.live(), fixture("live.json"))
        self.assertIn(("/v1/bridges/live", "ibu_live_example", USER_AGENT), FakeApi.seen)

    def test_errors_carry_the_code(self):
        with self.assertRaises(ApiError) as caught:
            self.client.bridges()
        self.assertEqual((caught.exception.status, caught.exception.code), (403, "trial_expired"))

    def test_rate_limit_carries_retry_after(self):
        with self.assertRaises(ApiError) as caught:
            self.client._get("/v1/anything")
        self.assertEqual((caught.exception.code, caught.exception.retry_after), ("rate_limited", 17))

    def test_every_error_shape(self):
        self.assertEqual(error_from(403, {"detail": {"error": "scope_required", "message": "m"}}).code, "scope_required")
        plain = error_from(401, {"detail": "Authentication required (API key or bearer token)"})
        self.assertEqual((plain.code, plain.message), (None, "Authentication required (API key or bearer token)"))
        listed = error_from(422, {"detail": [{"loc": ["query", "days"], "msg": "Input should be a valid integer"}]})
        self.assertEqual((listed.code, listed.message), ("invalid_request", "Input should be a valid integer"))
        self.assertEqual(error_from(502, None).code, None)
        self.assertEqual(error_from(404, {"detail": {"error": "bridge_not_found"}}).message, "")


if __name__ == "__main__":
    unittest.main()
