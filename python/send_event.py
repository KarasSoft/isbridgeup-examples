#!/usr/bin/env python3
"""Send a fixture event to your local receiver, signed the way IsBridgeUp
signs deliveries. Try duplicates, late events and bad signatures without
waiting for a real bridge.

  export IBU_WEBHOOK_SECRET=whsec_local_test   # the same value the receiver uses
  python3 send_event.py ../fixtures/events/brickell-lowered.json
  python3 send_event.py ../fixtures/events/brickell-lowered.json --times 2      # a retry: applied once
  python3 send_event.py ../fixtures/events/brickell-raised.json                 # older: ignored
  python3 send_event.py ../fixtures/events/brickell-lowered.json --age 600     # stale: 401
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def sign(secret: str, body: bytes, t: int) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("event", help="a JSON event, e.g. ../fixtures/events/brickell-lowered.json")
    parser.add_argument("--url", default="http://localhost:8080/webhooks/isbridgeup")
    parser.add_argument("--times", type=int, default=1, help="send the same delivery N times (retries)")
    parser.add_argument("--new-id", action="store_true", help="give the event a fresh id first")
    parser.add_argument("--age", type=int, default=0, help="sign as if sent this many seconds ago")
    args = parser.parse_args()

    secret = os.environ.get("IBU_WEBHOOK_SECRET", "")
    if not secret:
        print("Set IBU_WEBHOOK_SECRET to the receiver's signing secret.", file=sys.stderr)
        return 2
    with open(args.event, encoding="utf-8") as f:
        event = json.load(f)
    if args.new_id:
        event["id"] = str(uuid.uuid4())
    body = json.dumps(event, separators=(",", ":")).encode()
    delivery = str(uuid.uuid4())  # the same for every retry of this delivery

    for attempt in range(1, args.times + 1):
        request = urllib.request.Request(args.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-IsBridgeUp-Event": str(event.get("type")),
            "X-IsBridgeUp-Delivery": delivery,
            "X-IsBridgeUp-Signature": sign(secret, body, int(time.time()) - args.age),  # new t per attempt
        })
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except OSError as exc:
            print(f"Can't reach {args.url}: {exc}", file=sys.stderr)
            return 1
        print(f"attempt {attempt}: {event.get('type')} {event.get('id')} → HTTP {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
