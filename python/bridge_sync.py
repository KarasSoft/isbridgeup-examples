#!/usr/bin/env python3
"""bridge-sync: keep your dispatch tools in step with IsBridgeUp bridge states.

It reads the Live API at start-up, takes status.changed webhooks as they come,
reads the Live API again when an event asks for it and about every
ttl_seconds as a safety net, keeps everything in one SQLite file so a restart
loses nothing, and prints a board of your routes whenever a bridge changes.

  python3 bridge_sync.py --replay ../fixtures/scenarios/10-snapshot-after-recovery.json
  python3 bridge_sync.py --once     # needs IBU_KEY: one snapshot, print the board, exit
  python3 bridge_sync.py            # needs IBU_KEY; IBU_WEBHOOK_SECRET also starts the receiver

Settings (environment variables):
  IBU_KEY             your server key (ibu_live_…). Keep it out of code and history.
  IBU_API_BASE        default https://api.isbridgeup.com
  IBU_ROUTES          your routes file, default ../routes.example.json
  IBU_DB              the SQLite file, default ./bridge-sync.sqlite3
  IBU_WEBHOOK_SECRET  the endpoint's signing secret (whsec_…); starts the receiver
  IBU_PORT            receiver port, default 8080
  IBU_WEBHOOK_PATH    receiver path, default /webhooks/isbridgeup
  IBU_POLL_SECONDS    safety-net interval, default the API's ttl_seconds (at least 30)
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import pathlib
import signal
import sys
import threading
import time

from ibu.client import DEFAULT_BASE, ApiError, Client
from ibu.receiver import make_server
from ibu.scenarios import load, steps
from ibu.store import Store
from ibu.sync import Sync

HERE = pathlib.Path(__file__).resolve().parent
SEEN_IDS_KEPT_S = 3 * 24 * 3600   # longer than the ~24 h of automatic retries
REREAD_MIN_GAP_S = 5              # a burst of events causes one re-read, not many

HINTS = {
    "invalid_api_key": "check IBU_KEY: the whole key, not revoked (portal → API Keys)",
    "scope_required": "your plan has no Live API: it comes with Integrator and the trial",
    "trial_expired": "choose a plan in the portal (Billing); the key works again right away",
    "subscription_ended": "resume a plan in the portal (Billing); nothing was deleted",
    "rate_limited": "too many requests a minute; waiting for Retry-After",
    "volume_exceeded": "the request pool is used up for this period",
}


def log(line: str) -> None:
    print(time.strftime("%H:%M:%S"), line, flush=True)


def explain(err: ApiError) -> str:
    hint = HINTS.get(err.code or "")
    return f"API answered {err}" + (f": {hint}" if hint else "")


def key_problem(key: str) -> str | None:
    if not key:
        return "Set IBU_KEY to your server key (portal → API Keys), or try --replay without one."
    if key.startswith("whsec_"):
        return "IBU_KEY holds a signing secret. Use your server key there; the secret goes in IBU_WEBHOOK_SECRET."
    if key.startswith("ibu_dev_"):
        return "IBU_KEY holds a display token. bridge-sync needs a server key (ibu_live_…)."
    return None


def load_routes() -> tuple[list, pathlib.Path]:
    path = pathlib.Path(os.environ.get("IBU_ROUTES") or HERE.parent / "routes.example.json")
    return json.loads(path.read_text(encoding="utf-8"))["routes"], path


def replay(path: str) -> int:
    """Run a fixtures scenario: no key, no network."""
    routes, _ = load_routes()
    sync = Sync(routes, Store(":memory:"))
    print(load(path)["description"])
    for kind, body, label, at_ms in steps(path):
        if kind == "snapshot":
            sync.apply_snapshot(body, set_clock=False)
            note = ""
        else:
            note = "  → read the Live API again" if sync.apply_event(body) else ""
            label = f"{label} ({body.get('type')})"
        print(f"\n== {kind} {label}{note}")
        print(sync.board(at_ms))
    return 0


def refresh(sync: Sync, client: Client) -> float | None:
    """Read a snapshot. Returns how long to wait before the next try, or None
    for the normal interval."""
    try:
        sync.apply_snapshot(client.live())
        return None
    except ApiError as err:
        log(explain(err))
        if err.status == 429:
            return float(err.retry_after or 60)
        return 300.0 if err.status in (401, 403) else 60.0
    except (OSError, ValueError, http.client.HTTPException) as err:  # network, timeouts, a garbled answer
        log(f"couldn't read the Live API ({err.__class__.__name__}); trying again in 30 s")
        return 30.0


def run(once: bool) -> int:
    key = os.environ.get("IBU_KEY", "").strip()
    problem = key_problem(key)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    routes, routes_path = load_routes()
    client = Client(key, os.environ.get("IBU_API_BASE") or DEFAULT_BASE)
    store = Store(os.environ.get("IBU_DB") or str(HERE / "bridge-sync.sqlite3"))
    sync = Sync(routes, store)

    try:
        missing = sync.learn_catalog(client.bridges())
        snapshot = client.live()
    except ApiError as err:
        print(explain(err), file=sys.stderr)
        return 1
    except (OSError, ValueError, http.client.HTTPException) as err:
        print(f"Can't reach {client.base} ({err.__class__.__name__}: {err}).", file=sys.stderr)
        return 1
    if missing:
        log(f"not visible to this key, check {routes_path.name}: {', '.join(missing)}")
    sync.apply_snapshot(snapshot)
    print(sync.board(), flush=True)
    if once:
        return 0

    poll_s = float(os.environ.get("IBU_POLL_SECONDS") or max(30, int(snapshot.get("ttl_seconds") or 30)))
    wake, stop = threading.Event(), threading.Event()
    server = None
    secret = os.environ.get("IBU_WEBHOOK_SECRET", "").strip()
    if secret:
        port = int(os.environ.get("IBU_PORT") or 8080)
        path = os.environ.get("IBU_WEBHOOK_PATH") or "/webhooks/isbridgeup"
        server = make_server("0.0.0.0", port, path, secret, store, on_queued=wake.set, log=log)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"receiving webhooks on http://localhost:{port}{path}: register its public HTTPS URL in the portal")
    else:
        log(f"IBU_WEBHOOK_SECRET is not set: snapshots only, every {poll_s:.0f} s")

    def shutdown(*_):
        stop.set()
        wake.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    next_poll = time.monotonic() + poll_s
    last_reread = 0.0
    next_prune = 0.0
    shown = sync.fingerprint()
    if store.queued():
        wake.set()  # deliveries persisted before a restart
    while not stop.is_set():
        wake.wait(timeout=max(0.05, min(1.0, next_poll - time.monotonic())))
        wake.clear()
        wanted = sync.drain_inbox()
        now = time.monotonic()
        if (wanted and now - last_reread >= REREAD_MIN_GAP_S) or now >= next_poll:
            delay = refresh(sync, client)
            last_reread = time.monotonic()
            next_poll = last_reread + (delay if delay is not None else poll_s)
        elif wanted:
            next_poll = min(next_poll, last_reread + REREAD_MIN_GAP_S)
        if now >= next_prune:
            store.prune(sync.states, SEEN_IDS_KEPT_S)
            next_prune = now + 3600
        current = sync.fingerprint()
        if current != shown:
            print(sync.board(), flush=True)
            shown = current
    if server is not None:
        server.shutdown()
    store.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--once", action="store_true", help="read one snapshot, print the board and exit")
    parser.add_argument("--replay", metavar="SCENARIO", help="run a fixtures scenario offline (no key needed)")
    args = parser.parse_args()
    return replay(args.replay) if args.replay else run(args.once)


if __name__ == "__main__":
    sys.exit(main())
