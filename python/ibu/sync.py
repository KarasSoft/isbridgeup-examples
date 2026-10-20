"""One durable view of your bridges, fed by Live API snapshots and webhooks.

Two ages are kept apart. How old a bridge's state is belongs to IsBridgeUp:
while the API gives a state, it vouches for it, however old. How long ago the
Live API last answered belongs to you: when it hasn't for too long, or it
refused the key (401/403), every bridge is shown unknown until a read succeeds."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from .board import render, route_status
from .state import BridgeStates, revision_of

DEFAULT_MAX_OFFLINE_S = 90.0  # bridge_sync.py uses 3 × its poll interval

_NOTHING = BridgeStates()  # the view while we can't vouch: every bridge unknown


class Sync:
    def __init__(self, routes: list, store, *, max_offline_s: float | None = DEFAULT_MAX_OFFLINE_S):
        """max_offline_s: how long without a successful Live API read before
        every bridge shows as unknown. None turns the rule off (replays)."""
        self.routes = routes
        self.store = store
        self.states = BridgeStates()
        store.load(self.states)
        # When the Live API last answered (on its clock), and the error code
        # it refused the key with, until a read succeeds again.
        self.last_sync_ms, self.access_lost = store.load_link()
        self.max_offline_s = max_offline_s
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
            read_at = revision_of(snapshot.get("server_time")) or self.now_ms()
            self.last_sync_ms = max(read_at, self.last_sync_ms or 0)
            self.access_lost = None
            self.store.save(self.states)
            self.store.save_link(self.last_sync_ms, self.access_lost)

    def lose_access(self, code: str) -> None:
        """The Live API refused the key (401/403): stop vouching right away."""
        with self._lock:
            self.access_lost = code
            self.store.save_link(self.last_sync_ms, self.access_lost)

    def link_problem(self, now_ms: int | None = None) -> str | None:
        """Why we can't vouch for any bridge right now, or None."""
        now_ms = self.now_ms() if now_ms is None else now_ms
        if self.access_lost:
            return f"IsBridgeUp refused the key ({self.access_lost})"
        if self.last_sync_ms is None:
            return "no answer from IsBridgeUp yet"
        if self.max_offline_s is not None and now_ms - self.last_sync_ms > self.max_offline_s * 1000:
            minutes = (now_ms - self.last_sync_ms) // 60000
            last = datetime.fromtimestamp(self.last_sync_ms / 1000, timezone.utc).strftime("%H:%M")
            return f"no answer from IsBridgeUp for {minutes} min (last read {last} UTC)"
        return None

    def _view(self, now_ms: int):
        return _NOTHING if self.link_problem(now_ms) else self.states

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

    def fingerprint(self, now_ms: int | None = None) -> dict:
        """What the board shows, without the minutes: print again when it changes."""
        now_ms = self.now_ms() if now_ms is None else now_ms
        with self._lock:
            view = self._view(now_ms)
            return {"link": self.link_problem(now_ms) is None,
                    **{bid: b["state"] for bid, b in view.bridges.items()}}

    def route_statuses(self, now_ms: int | None = None) -> dict:
        """CLEAR / CHECK / BLOCKED per route: what to hand your dispatch tools."""
        now_ms = self.now_ms() if now_ms is None else now_ms
        with self._lock:
            view = self._view(now_ms)
            return {r["name"]: route_status(view, r["bridges"]) for r in self.routes}

    def board(self, now_ms: int | None = None) -> str:
        now_ms = self.now_ms() if now_ms is None else now_ms
        with self._lock:
            problem = self.link_problem(now_ms)
            board = render(self.routes, _NOTHING if problem else self.states, self.names, self.durations, now_ms)
            if problem:
                board = f"NO LINK  {problem}: every bridge is unknown until a read succeeds\n{board}"
            return board
