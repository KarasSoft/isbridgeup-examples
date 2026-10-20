from __future__ import annotations  # Python 3.7+

import hashlib
import hmac
import re
import time


def verify(secret: str, raw_body: bytes, header: str | None, tolerance: int = 300) -> bool:
    """True only for an authentic, fresh delivery. Never raises on bad input.

    secret:   the endpoint's signing secret (whsec_…), whole, not your API key
    raw_body: the request body exactly as received, before JSON parsing
    header:   the X-IsBridgeUp-Signature header, e.g. "t=1792159121,v1=5f2a…"
    """
    if not isinstance(secret, str) or not secret or not isinstance(header, str) \
            or not isinstance(raw_body, (bytes, bytearray)):
        return False
    parts = {}
    for item in header.split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts[key] = value
    ts, sig = parts.get("t", ""), parts.get("v1", "")
    if not re.fullmatch(r"[0-9]{1,12}", ts) or not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False
    if abs(time.time() - int(ts)) > tolerance:
        return False  # too old or from the future: possible replay
    key = secret.encode("utf-8")  # the whole secret, whsec_ included: nothing to decode
    expected = hmac.new(key, ts.encode() + b"." + bytes(raw_body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
