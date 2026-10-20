"""verify() accepts what IsBridgeUp signs and rejects everything else, without raising."""

import json
import time
import unittest

from ibu.verify import verify
from send_event import sign

from .helpers import SECRET, fixture

BODY = json.dumps(fixture("events/brickell-lowered.json"), separators=(",", ":")).encode()


class Verify(unittest.TestCase):
    def test_valid_delivery(self):
        self.assertTrue(verify(SECRET, BODY, sign(SECRET, BODY, int(time.time()))))
        self.assertTrue(verify(SECRET, bytearray(BODY), sign(SECRET, BODY, int(time.time()))))

    def test_rejections(self):
        now = int(time.time())
        good = sign(SECRET, BODY, now)
        sig = good.split("v1=")[1]
        cases = {
            "wrong secret": ("whsec_other", BODY, good),
            "secret without its prefix": (SECRET[len("whsec_"):], BODY, good),
            "tampered body": (SECRET, BODY.replace(b"CLEAR", b"UP"), good),
            "re-serialized body": (SECRET, json.dumps(json.loads(BODY), indent=2).encode(), good),
            "stale": (SECRET, BODY, sign(SECRET, BODY, now - 3600)),
            "from the future": (SECRET, BODY, sign(SECRET, BODY, now + 3600)),
            "missing header": (SECRET, BODY, None),
            "empty header": (SECRET, BODY, ""),
            "garbage": (SECRET, BODY, "garbage"),
            "no timestamp": (SECRET, BODY, f"v1={sig}"),
            "no signature": (SECRET, BODY, f"t={now}"),
            "short signature": (SECRET, BODY, f"t={now},v1=00"),
            "upper-case hex": (SECRET, BODY, f"t={now},v1={sig.upper()}"),
            "non-numeric t": (SECRET, BODY, f"t=abc,v1={sig}"),
            "huge t": (SECRET, BODY, f"t={'9' * 400},v1={sig}"),
            "parsed body": (SECRET, json.loads(BODY), good),
            "str body": (SECRET, BODY.decode(), good),
            "bytes header": (SECRET, BODY, good.encode()),
            "int header": (SECRET, BODY, 123),
            "no secret": (None, BODY, good),
            "empty secret": ("", BODY, good),
        }
        for name, (secret, body, header) in cases.items():
            with self.subTest(name):
                self.assertIs(verify(secret, body, header), False)


if __name__ == "__main__":
    unittest.main()
