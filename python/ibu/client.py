"""A minimal IsBridgeUp REST client: the standard library, a timeout on every
call, and errors you can branch on."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.isbridgeup.com"
USER_AGENT = "isbridgeup-examples/1.0 (python)"


class ApiError(Exception):
    """A non-2xx answer. Branch on `code` (detail.error), never on the message."""

    def __init__(self, status: int, code: str | None, message: str, retry_after: int | None = None):
        super().__init__(f"HTTP {status}" + (f" {code}" if code else "") + (f": {message}" if message else ""))
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


def error_from(status: int, body, retry_after: int | None = None) -> ApiError:
    """Errors share one shape, {"detail": {"error": …, "message": …}}, but a few
    answers differ: a plain-string detail (no X-API-Key header, a server error)
    and a list of problems (an invalid query parameter). Check before reading."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return ApiError(status, detail.get("error"), detail.get("message") or "", retry_after)
    if isinstance(detail, list):
        problems = "; ".join(str(p.get("msg", p)) if isinstance(p, dict) else str(p) for p in detail)
        return ApiError(status, "invalid_request", problems, retry_after)
    return ApiError(status, None, detail if isinstance(detail, str) else "", retry_after)


class Client:
    """GET /v1/bridges and GET /v1/bridges/live with a server key."""

    def __init__(self, key: str, base: str = DEFAULT_BASE, timeout: float = 10.0):
        self.key = key
        self.base = base.rstrip("/")
        self.timeout = timeout

    def bridges(self) -> dict:
        return self._get("/v1/bridges")

    def live(self) -> dict:
        return self._get("/v1/bridges/live")

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(self.base + path, headers={
            "X-API-Key": self.key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read() or b"null")
            except ValueError:
                body = None
            retry_after = exc.headers.get("Retry-After", "")
            raise error_from(exc.code, body, int(retry_after) if retry_after.isdigit() else None) from None
