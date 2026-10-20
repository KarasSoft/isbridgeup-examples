"""Read the scenarios in fixtures/scenarios/: the replay mode and the tests use them."""

from __future__ import annotations

import json
import pathlib

from .state import revision_of


def load(path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def steps(path):
    """Yield (kind, body, label, at_ms) per step, at_ms being the moment the
    step happens (for the board's minutes and the time since the last read):
      snapshot   a Live API answer (file name or inline)
      event      a verified webhook (file name or inline)
      api_error  a failed Live API read: {"status": 403, "code": "…", "at": "<ISO time>"}
      clock      time passes: "<ISO time>"
    """
    fixtures = pathlib.Path(path).resolve().parent.parent
    at_ms = 0
    for step in load(path)["steps"]:
        (kind, ref), = step.items()
        if kind == "clock":
            body, label, moment = ref, f"at {ref}", ref
        elif kind == "api_error":
            body, label, moment = ref, f"HTTP {ref['status']} {ref.get('code') or ''}".rstrip(), ref.get("at")
        else:
            body = load(fixtures / ref) if isinstance(ref, str) else ref
            label = ref if isinstance(ref, str) else "(inline)"
            moment = body.get("server_time") if kind == "snapshot" else body.get("created_at")
        if moment:
            at_ms = max(at_ms, revision_of(moment))
        yield kind, body, label, at_ms
