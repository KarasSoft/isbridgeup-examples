"""Every scenario in fixtures/scenarios/ ends in the state it promises."""

import unittest

from bridge_sync import read_failed
from ibu.scenarios import load, steps
from ibu.state import normalize_state, revision_of
from ibu.store import Store
from ibu.sync import Sync

from .helpers import FIXTURES, ROUTES, SCENARIOS, fixture


def run(path):
    """Play a scenario. Returns the Sync, what each event asked for, and the
    scenario's last moment (route statuses are taken then)."""
    sync = Sync(ROUTES, Store(":memory:"), max_offline_s=load(path).get("max_offline_seconds"))
    reread, at_ms = [], 0
    for kind, body, _label, at_ms in steps(path):
        if kind == "snapshot":
            sync.apply_snapshot(body, set_clock=False)
        elif kind == "event":
            reread.append(sync.apply_event(body))
        elif kind == "api_error":
            read_failed(sync, body["status"], body.get("code"))
    return sync, reread, at_ms


class Scenarios(unittest.TestCase):
    def test_every_scenario_ends_as_described(self):
        self.assertGreaterEqual(len(SCENARIOS), 10)
        for path in SCENARIOS:
            with self.subTest(path.name):
                expect = load(path)["expect"]
                sync, reread, at_ms = run(path)
                self.assertEqual(sync.states.bridges, expect["bridges"])
                self.assertEqual(reread, expect["reread"])
                self.assertEqual(sync.route_statuses(at_ms), expect["routes"])

    def test_board_matches_the_sample(self):
        sync, _, _ = run(SCENARIOS[0])
        now = revision_of(fixture("live.json")["server_time"])
        self.assertEqual(sync.board(now), (FIXTURES / "board-01.txt").read_text(encoding="utf-8").rstrip("\n"))

    def test_unknown_values_are_never_lowered(self):
        for value in (None, "UNKNOWN", "DOWN", "clear", "", 0, "SOMETHING_NEW"):
            self.assertEqual(normalize_state(value), "UNKNOWN")
        self.assertEqual(normalize_state("CLEAR"), "CLEAR")

    def test_revision_is_state_changed_at_in_ms(self):
        event = fixture("events/brickell-raised.json")["data"]
        self.assertEqual(revision_of(event["state_changed_at"]), event["revision"])
        self.assertEqual(revision_of("2026-10-16T13:58:40Z"), event["revision"])


if __name__ == "__main__":
    unittest.main()
