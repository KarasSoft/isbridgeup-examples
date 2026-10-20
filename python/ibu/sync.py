"""One durable view of your bridges, fed by Live API snapshots and webhooks."""

from __future__ import annotations

import json
import threading
import time

from .board import render, route_status
from .state import BridgeStates, revision_of


class Sync:
    def __init__(self, routes: list, store):
        self.routes = routes
        self.store = store
        self.states = BridgeStates()
        store.load(self.states)
        self.names: dict = {}
        self.durations: dict = {}
        self.offset_ms = 0  # the API's clock minus ours, from the last snapshot
        self._lock = threading.Lock()

    def now_ms(self) -> int:
        return int(time.time() * 1000) + self.offset_ms

    def learn_catalog(self, catalog: dict) -> list:
        """Names from GET /v1/bridges. Returns the route bridge ids the key can't see."""
        visible = set()
        with self._lock:
            for b in catalog.get("bridges", []):
                self.names[b["id"]] = b.get("name") or b["id"]
                visible.add(b["id"])
        return sorted({bid for route in self.routes for bid in route["bridges"]} - visible)

    def apply_snapshot(self, snapshot: dict, *, set_clock: bool = True) -> None:
        with self._lock:
            if set_clock and snapshot.get("server_time"):
                # Minutes are counted on the API's clock, not ours (ours may be off).
                self.offset_ms = revision_of(snapshot["server_time"]) - int(time.time() * 1000)
            for b in snapshot.get("bridges", []):
                self.names.setdefault(b["id"], b.get("name") or b["id"])
                if "raise_duration" in b:  # only on plans that include it
                    self.durations[b["id"]] = b["raise_duration"]
            self.states.apply_snapshot(snapshot)
            self.store.save(self.states)

    def apply_event(self, event: dict, *, seq: int | None = None) -> bool:
        """Apply one verified event. True when the Live API should be read again."""
        with self._lock:
            reread = self.states.apply_event(event)
            self.store.save(self.states, done_seq=seq, event_id=event.get("id"))
            return reread

    def drain_inbox(self) -> bool:
        """Apply every queued delivery in arrival order. True if any asks for a re-read."""
        reread = False
        while True:
            item = self.store.next_queued()
            if item is None:
                return reread
            seq, body = item
            reread = self.apply_event(json.loads(body), seq=seq) or reread

    def fingerprint(self) -> dict:
        with self._lock:
            return {bid: b["state"] for bid, b in self.states.bridges.items()}

    def route_statuses(self) -> dict:
        with self._lock:
            return {r["name"]: route_status(self.states, r["bridges"]) for r in self.routes}

    def board(self, now_ms: int | None = None) -> str:
        with self._lock:
            return render(self.routes, self.states, self.names, self.durations,
                          self.now_ms() if now_ms is None else now_ms)
