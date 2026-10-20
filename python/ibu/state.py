import time
from datetime import datetime


def normalize_state(value):
    """REST sends "UP" | "CLEAR" | null; webhooks send "UP" | "CLEAR" | "UNKNOWN".
    Anything else is unknown too: never read an unexpected value as CLEAR."""
    return value if value in ("UP", "CLEAR") else "UNKNOWN"


def revision_of(state_changed_at):
    """A REST item's revision: state_changed_at in epoch milliseconds, the same
    number a status.changed webhook carries for that change."""
    if not state_changed_at:
        return None
    return int(datetime.fromisoformat(state_changed_at.replace("Z", "+00:00")).timestamp() * 1000)


class BridgeStates:
    """The latest state per bridge, merged from webhooks and REST snapshots.
    In production keep `bridges` and `seen` in your database, so a restart
    neither forgets state nor applies an event twice."""

    def __init__(self):
        # bridge_id -> {"state": "UP" | "CLEAR" | "UNKNOWN", "revision": int | None, "hold": bool}
        self.bridges = {}
        self.seen = {}  # event id -> when we handled it (epoch seconds)

    def _apply(self, bridge_id, state, revision):
        held = self.bridges.get(bridge_id, {}).get("revision")
        if revision is None or (held is not None and revision < held):
            return  # order unknown, or older than what we hold
        self.bridges[bridge_id] = {"state": state, "revision": revision, "hold": False}

    def _hold_unknown(self, bridge_id, revision=None):
        # We can't vouch for this bridge. Show unknown, remember the newest
        # revision seen, and wait for a snapshot that shows a state again.
        held = self.bridges.get(bridge_id, {}).get("revision")
        known = [r for r in (held, revision) if r is not None]
        self.bridges[bridge_id] = {"state": "UNKNOWN", "revision": max(known, default=None), "hold": True}

    def apply_snapshot(self, snapshot):
        """GET /v1/bridges/live: at start-up, when apply_event asks for it,
        and about every ttl_seconds as a safety net."""
        for b in snapshot["bridges"]:
            revision = revision_of(b.get("state_changed_at"))
            if b.get("state") is None:
                self._hold_unknown(b["id"], revision)
            else:
                self._apply(b["id"], normalize_state(b["state"]), revision)

    def apply_event(self, event):
        """A verified webhook. Returns True when you should re-read the Live
        API and pass the result to apply_snapshot."""
        if event["id"] in self.seen:
            return False  # duplicate delivery
        self.seen[event["id"]] = time.time()
        data = event.get("data") or {}
        if data.get("test"):
            return False  # the portal's test event carries no bridge
        if event["type"] == "status.changed":
            bridge_id, state = data["bridge_id"], normalize_state(data.get("state"))
            if state == "UNKNOWN" or self.bridges.get(bridge_id, {}).get("hold"):
                # Only a snapshot lifts an unknown: a feed can go quiet without
                # a new event, so a retried event can be older news than it.
                self._hold_unknown(bridge_id, data.get("revision"))
                return True
            self._apply(bridge_id, state, data.get("revision"))
        elif event["type"] == "source.outage":
            # Each event lists every bridge of yours still affected. One that
            # drops off the list gets no "recovered" event, and "recovered"
            # never means CLEAR: re-read after any outage event.
            if data.get("status") == "degraded":
                for bridge_id in data.get("bridge_ids", []):
                    self._hold_unknown(bridge_id)
            return True
        return False

    def prune(self, max_age_s=3 * 24 * 3600):
        """Forget handled event ids older than your retry window."""
        cutoff = time.time() - max_age_s
        self.seen = {k: t for k, t in self.seen.items() if t >= cutoff}
