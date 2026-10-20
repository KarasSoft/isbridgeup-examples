"""A restart loses nothing: states, handled ids and queued deliveries survive."""

import json
import os
import tempfile
import time
import unittest

from ibu.state import revision_of
from ibu.store import Store
from ibu.sync import Sync

from .helpers import BRICKELL, FREMONT, ROUTES, fixture


class Restart(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.remove(self.path + suffix)

    def test_state_and_handled_ids_survive_a_restart(self):
        lowered = fixture("events/brickell-lowered.json")
        store = Store(self.path)
        sync = Sync(ROUTES, store)
        sync.apply_snapshot(fixture("live.json"), set_clock=False)
        sync.apply_event(lowered)
        before = dict(sync.states.bridges)
        store.close()

        store = Store(self.path)
        sync = Sync(ROUTES, store)
        self.assertEqual(sync.states.bridges, before)
        self.assertIn(lowered["id"], sync.states.seen)
        self.assertFalse(sync.apply_event(lowered))          # a retry after the restart: ignored
        self.assertEqual(sync.states.bridges, before)
        store.close()

    def test_queued_deliveries_are_applied_after_a_restart(self):
        lowered = fixture("events/brickell-lowered.json")
        store = Store(self.path)
        Sync(ROUTES, store).apply_snapshot(fixture("live.json"), set_clock=False)
        self.assertTrue(store.enqueue(lowered["id"], json.dumps(lowered)))
        self.assertFalse(store.enqueue(lowered["id"], json.dumps(lowered)))   # already queued
        store.close()                                                         # stopped before the worker ran

        store = Store(self.path)
        sync = Sync(ROUTES, store)
        self.assertEqual(store.queued(), 1)
        self.assertFalse(sync.drain_inbox())
        self.assertEqual(store.queued(), 0)
        self.assertEqual(sync.states.bridges[BRICKELL]["state"], "CLEAR")
        store.close()

    def test_old_handled_ids_are_pruned(self):
        store = Store(self.path)
        sync = Sync(ROUTES, store)
        sync.apply_event(fixture("events/test-event.json"))                 # handled now
        sync.states.seen["handled-ten-days-ago"] = time.time() - 10 * 24 * 3600
        store.save(sync.states, event_id="handled-ten-days-ago")
        store.prune(sync.states, 3 * 24 * 3600)
        self.assertNotIn("handled-ten-days-ago", sync.states.seen)
        self.assertEqual(len(sync.states.seen), 1)
        store.close()
        self.assertEqual(len(Sync(ROUTES, Store(self.path)).states.seen), 1)


    def test_a_restart_with_an_old_cache_shows_nothing_as_lowered(self):
        """Regression audit R1: the cache says CLEAR, but the last read was long
        ago (or the key was refused): CHECK until a read succeeds again."""
        snap = fixture("live-after-recovery.json")
        read_at = revision_of(snap["server_time"])
        store = Store(self.path)
        Sync(ROUTES, store, max_offline_s=90).apply_snapshot(snap, set_clock=False)
        store.close()

        store = Store(self.path)
        sync = Sync(ROUTES, store, max_offline_s=90)
        self.assertEqual(sync.route_statuses(read_at + 60_000)["Brickell loop"], "CLEAR")
        self.assertEqual(set(sync.route_statuses(read_at + 3_600_000).values()), {"CHECK"})
        sync.lose_access("subscription_ended")
        store.close()

        store = Store(self.path)
        sync = Sync(ROUTES, store, max_offline_s=90)
        self.assertEqual(sync.access_lost, "subscription_ended")
        self.assertEqual(set(sync.route_statuses(read_at + 1000).values()), {"CHECK"})
        store.close()

    def test_a_bridge_a_snapshot_dropped_stays_held_after_a_restart(self):
        """Re-audit A2: scope drop → restart → a delayed CLEAR webhook must not
        bring the bridge back; only a snapshot that lists it again does."""
        store = Store(self.path)
        sync = Sync(ROUTES, store, max_offline_s=None)
        sync.apply_snapshot(fixture("live-after-recovery.json"), set_clock=False)
        sync.apply_snapshot(fixture("live-scope-changed.json"), set_clock=False)
        store.close()

        store = Store(self.path)
        sync = Sync(ROUTES, store, max_offline_s=None)
        self.assertTrue(sync.states.bridges[FREMONT]["hold"])
        self.assertTrue(sync.apply_event(fixture("events/fremont-feed-back.json")))   # asks for a re-read
        self.assertEqual(sync.route_statuses()["Fremont crossing"], "CHECK")
        sync.apply_snapshot(fixture("live-later.json"), set_clock=False)               # listed again
        self.assertEqual(sync.route_statuses()["Fremont crossing"], "CLEAR")
        store.close()

if __name__ == "__main__":
    unittest.main()
