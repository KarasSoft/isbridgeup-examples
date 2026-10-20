"""The command-line service: key checks, replay, and how it backs off."""

import contextlib
import http.client
import io
import unittest

import bridge_sync
from ibu.client import ApiError
from ibu.state import revision_of
from ibu.store import Store
from ibu.sync import Sync

from .helpers import ROUTES, SCENARIOS, fixture


class FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome

    def live(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class BridgeSyncTest(unittest.TestCase):
    def test_key_checks(self):
        self.assertIn("IBU_KEY", bridge_sync.key_problem(""))
        self.assertIn("signing secret", bridge_sync.key_problem("whsec_abc"))
        self.assertIn("display token", bridge_sync.key_problem("ibu_dev_abc"))
        self.assertIsNone(bridge_sync.key_problem("ibu_live_abc"))

    def test_replay_runs_every_scenario(self):
        for path in SCENARIOS:
            with self.subTest(path.name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(bridge_sync.replay(str(path)), 0)
                self.assertRegex(out.getvalue(), r"== snapshot live[\w-]*\.json")

    def test_refresh_backs_off(self):
        sync = Sync(ROUTES, Store(":memory:"))
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet:
            self.assertIsNone(bridge_sync.refresh(sync, FakeClient(fixture("live.json"))))
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(ApiError(429, "rate_limited", "", 12))), 12)
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(ApiError(403, "trial_expired", ""))), 300)
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(ApiError(503, None, ""))), 60)
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(TimeoutError("timed out"))), 30)
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(http.client.IncompleteRead(b""))), 30)
            self.assertEqual(bridge_sync.refresh(sync, FakeClient(ValueError("not JSON"))), 30)
        self.assertEqual(len(sync.states.bridges), 3)

    def test_losing_the_api_never_leaves_a_stale_clear(self):
        """Regression audit R1: a refused key turns every route CHECK at once;
        timeouts, 5xx and 429 do once nothing has answered for max_offline_s;
        the next successful read brings the board back."""
        snap = fixture("live-after-recovery.json")                  # every route CLEAR
        read_at = revision_of(snap["server_time"])
        all_clear = {"Brickell loop": "CLEAR", "Fremont crossing": "CLEAR"}
        all_check = {"Brickell loop": "CHECK", "Fremont crossing": "CHECK"}
        quiet = contextlib.redirect_stdout(io.StringIO())
        for failure in (ApiError(401, "invalid_api_key", ""), ApiError(403, "subscription_ended", ""),
                        ApiError(403, "scope_required", "")):
            with self.subTest(failure=str(failure)):
                sync = Sync(ROUTES, Store(":memory:"), max_offline_s=90)
                sync.apply_snapshot(snap, set_clock=False)
                with quiet:
                    self.assertEqual(bridge_sync.refresh(sync, FakeClient(failure)), 300)
                self.assertEqual(sync.route_statuses(read_at + 1000), all_check)   # at once
                self.assertIn("refused the key", sync.board(read_at + 1000))
                sync.apply_snapshot(snap, set_clock=False)                          # access is back
                self.assertEqual(sync.route_statuses(read_at + 1000), all_clear)
        for failure in (TimeoutError("timed out"), OSError("connection refused"),
                        ApiError(503, None, ""), ApiError(429, "rate_limited", "", 30)):
            with self.subTest(failure=repr(failure)):
                sync = Sync(ROUTES, Store(":memory:"), max_offline_s=90)
                sync.apply_snapshot(snap, set_clock=False)
                for _ in range(3):                                                  # a series of failed reads
                    with quiet:
                        bridge_sync.refresh(sync, FakeClient(failure))
                self.assertEqual(sync.route_statuses(read_at + 90_000), all_clear)  # not for long yet
                self.assertEqual(sync.route_statuses(read_at + 90_001), all_check)  # too long: CHECK
                self.assertIn("no answer from IsBridgeUp for 1 min", sync.board(read_at + 90_001))
                with quiet:
                    self.assertIsNone(bridge_sync.refresh(sync, FakeClient(fixture("live-later.json"))))
                later = revision_of(fixture("live-later.json")["server_time"])
                self.assertEqual(sync.route_statuses(later), all_clear)

    def test_the_board_reprints_when_the_link_is_lost(self):
        sync = Sync(ROUTES, Store(":memory:"), max_offline_s=90)
        snap = fixture("live-after-recovery.json")
        sync.apply_snapshot(snap, set_clock=False)
        read_at = revision_of(snap["server_time"])
        self.assertNotEqual(sync.fingerprint(read_at), sync.fingerprint(read_at + 91_000))

    def test_api_errors_are_explained(self):
        self.assertIn("Integrator", bridge_sync.explain(ApiError(403, "scope_required", "")))


if __name__ == "__main__":
    unittest.main()
