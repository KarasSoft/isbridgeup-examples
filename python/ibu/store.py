"""Durable state in one SQLite file: the state per bridge, the event ids
already handled, and the webhook inbox.

The inbox is what makes it safe to answer a delivery quickly: the receiver
writes the verified event here, answers 2xx, and the worker applies it
afterwards. If the process stops in between, the event is still in the
inbox on the next start."""

from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_state (
    bridge_id TEXT PRIMARY KEY,
    state     TEXT NOT NULL,
    revision  INTEGER,
    hold      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_event (
    event_id   TEXT PRIMARY KEY,
    handled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    body        TEXT NOT NULL,
    received_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        # One connection shared by the receiver and the worker, behind a lock.
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def load(self, states) -> None:
        """Fill a BridgeStates with what was saved before a restart."""
        with self._lock:
            for bridge_id, state, revision, hold in self._db.execute(
                "SELECT bridge_id, state, revision, hold FROM bridge_state"
            ):
                states.bridges[bridge_id] = {"state": state, "revision": revision, "hold": bool(hold)}
            states.seen.update(self._db.execute("SELECT event_id, handled_at FROM seen_event").fetchall())

    def enqueue(self, event_id: str, body: str) -> bool:
        """Persist a verified delivery. False when that event is already queued."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO inbox (event_id, body, received_at) VALUES (?, ?, ?)",
                (event_id, body, time.time()),
            )
            return cursor.rowcount == 1

    def next_queued(self) -> tuple[int, str] | None:
        """(seq, raw JSON body) of the oldest queued event, or None."""
        with self._lock:
            return self._db.execute("SELECT seq, body FROM inbox ORDER BY seq LIMIT 1").fetchone()

    def queued(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def save(self, states, *, done_seq: int | None = None, event_id: str | None = None) -> None:
        """In one transaction: every bridge, the event id just handled, and the
        removal of the inbox row it came from."""
        rows = [(bid, b["state"], b["revision"], int(bool(b.get("hold")))) for bid, b in states.bridges.items()]
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.executemany("INSERT OR REPLACE INTO bridge_state VALUES (?, ?, ?, ?)", rows)
                if event_id is not None and event_id in states.seen:
                    self._db.execute("INSERT OR IGNORE INTO seen_event VALUES (?, ?)", (event_id, states.seen[event_id]))
                if done_seq is not None:
                    self._db.execute("DELETE FROM inbox WHERE seq = ?", (done_seq,))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def prune(self, states, max_age_s: float) -> None:
        """Forget handled event ids older than your retry window."""
        states.prune(max_age_s)
        with self._lock:
            self._db.execute("DELETE FROM seen_event WHERE handled_at < ?", (time.time() - max_age_s,))

    def close(self) -> None:
        with self._lock:
            self._db.close()
