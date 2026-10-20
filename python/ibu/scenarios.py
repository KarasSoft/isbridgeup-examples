"""Read the scenarios in fixtures/scenarios/: the replay mode and the tests use them."""

from __future__ import annotations

import json
import pathlib

from .state import revision_of


def load(path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def steps(path):
    """Yield (kind, body, label, at_ms) per step: kind is "snapshot" or
    "event", at_ms the moment the step happens (for the board's minutes)."""
    fixtures = pathlib.Path(path).resolve().parent.parent
    at_ms = 0
    for step in load(path)["steps"]:
        (kind, ref), = step.items()
        body = load(fixtures / ref) if isinstance(ref, str) else ref
        moment = body.get("server_time") if kind == "snapshot" else body.get("created_at")
        if moment:
            at_ms = max(at_ms, revision_of(moment))
        yield kind, body, ref if isinstance(ref, str) else "(inline)", at_ms
