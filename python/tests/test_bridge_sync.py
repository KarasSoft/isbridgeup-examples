"""The command-line service: key checks, replay, and how it backs off."""

import contextlib
import http.client
import io
import unittest

import bridge_sync
from ibu.client import ApiError
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
                self.assertIn("== snapshot live.json", out.getvalue())

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

    def test_api_errors_are_explained(self):
        self.assertIn("Integrator", bridge_sync.explain(ApiError(403, "scope_required", "")))


if __name__ == "__main__":
    unittest.main()
