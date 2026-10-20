# IsBridgeUp API examples

Runnable examples for the [IsBridgeUp API](https://dash.isbridgeup.com/docs/): live drawbridge
state in your backend, webhooks, and the rules that keep the two in step. Python and Node.js,
no dependencies.

- **bridge-sync**: a small service for delivery and mobility backends. It reads the Live API,
  takes `status.changed` webhooks, reads the Live API again when an event asks for it and on a
  safety-net interval, keeps its state in one file so a restart loses nothing, and prints a
  board of your routes.
- **verify**: the webhook signature check, the same code as in the docs.
- **send-event**: signs a sample event and posts it to your local receiver, so you can try
  retries, late events and bad signatures without waiting for a real bridge.
- **fixtures**: API responses and webhook events in the exact v1 format, and fourteen scenarios
  with the result each one must end in. Both languages run all of them in their tests.

The service decides nothing about routing: it tells your systems which bridges are raised,
lowered or unknown. What a dispatcher or a router does with that is yours.

## Try it without a key

```bash
git clone https://github.com/KarasSoft/isbridgeup-examples.git
cd isbridgeup-examples/python
python3 bridge_sync.py --replay ../fixtures/scenarios/10-snapshot-after-recovery.json
```

Node.js: `cd node && node bridge-sync.mjs --replay ../fixtures/scenarios/10-snapshot-after-recovery.json`.
Both print the same thing. The first board of every scenario looks like this:

```text
BLOCKED  Brickell loop
  Brickell Avenue Bridge: raised for 3 min (past raises here: median 8 min, 9 in 10 within 14 min)
  SW 2nd Avenue Bridge: lowered for 135 min
CHECK    Fremont crossing
  Fremont Bridge: unknown, we can't vouch for it right now
```

A route is **BLOCKED** when one of its bridges is raised, **CHECK** when we can't vouch for one
of them, and **CLEAR** only when every bridge on it is lowered. The raise durations are history,
not a countdown.

## The scenarios

| Scenario | What happens | Ends with |
| --- | --- | --- |
| `01-start-from-snapshot` | Read the Live API | Brickell raised, SW 2nd lowered, Fremont unknown (its feed is down) |
| `02-newer-event-applies` | `status.changed`: Brickell lowered | Brickell lowered |
| `03-duplicate-ignored` | The same delivery twice (a retry) | Applied once |
| `04-out-of-order-ignored` | The older raise arrives after the lowering | Still lowered |
| `05-older-snapshot-ignored` | A snapshot built before the event | Still lowered |
| `06-test-event-ignored` | The portal's **Send test event** | Nothing changes |
| `07-unexpected-value-is-unknown` | A state value the client doesn't know | Unknown, never lowered |
| `08-unknown-held-until-snapshot` | Events for a bridge the API can't vouch for, even "lowered" | Unknown until a snapshot shows a state |
| `09-recovered-is-not-clear` | `source.outage` degraded, then recovered | Still unknown: recovered is not lowered |
| `10-snapshot-after-recovery` | The re-read after recovery | Every bridge known again, routes clear |
| `11-refused-key-is-check` | The Live API answers `403 subscription_ended` | Every route CHECK at once |
| `12-no-answer-is-check` | Reads fail (`503`, then silence) | Unchanged at first, every route CHECK after 90 s without an answer |
| `13-read-again-lifts-check` | A successful read after a refused key | Routes clear again |
| `14-bridge-left-scope-is-unknown` | A snapshot no longer lists Fremont (the plan changed) | Fremont forgotten, its route CHECK |

## Run it with your key

You need Python 3.9 or newer, or Node.js 22 or newer, and a server key whose plan includes the
Live API (the 7-day trial and Integrator do).

1. In the portal open **API Keys** and create a key. It starts with `ibu_live_`. It is a server
   key: it stays on your backend.
2. Read the live state once:

   ```bash
   cd python                                # or: cd node
   read -rs IBU_KEY && export IBU_KEY       # paste the key, press Enter; it stays out of your history
   python3 bridge_sync.py --once            # or: node bridge-sync.mjs --once
   ```

3. Put your own routes in a file shaped like [`routes.example.json`](routes.example.json), with
   bridge ids from `GET /v1/bridges`. Ids never change. Then `export IBU_ROUTES=path/to/routes.json`.
4. Run it: `python3 bridge_sync.py` (or `node bridge-sync.mjs`). Without a webhook secret it reads
   the Live API every `ttl_seconds`; stop it with Ctrl+C.

## Receive webhooks

Start the receiver with any secret and feed it signed sample events:

```bash
export IBU_WEBHOOK_SECRET=whsec_local_test
python3 bridge_sync.py                    # terminal 1: receiver on http://localhost:8080/webhooks/isbridgeup

export IBU_WEBHOOK_SECRET=whsec_local_test
python3 send_event.py ../fixtures/events/brickell-lowered.json             # terminal 2: HTTP 204, accepted
python3 send_event.py ../fixtures/events/brickell-lowered.json --times 2   # a retry: 204 twice, handled once
python3 send_event.py ../fixtures/events/brickell-lowered.json --age 600   # a stale signature: HTTP 401
python3 send_event.py ../fixtures/events/outage-degraded.json              # the service reads the Live API again
```

(`node send-event.mjs …` takes the same arguments.)

`204` means the delivery was verified and stored, not that the board changed. The sample events
are from 16 October 2026, and your board starts from today's Live API state, which is newer: the
service rightly ignores an event older than the state it holds. To watch the board change step by
step, replay a scenario (`--replay`, above); with a live endpoint, real changes arrive on their own.

For real deliveries:

1. Expose port 8080 over HTTPS. For a test from a laptop, use a tunnel such as `cloudflared` or
   `ngrok`; we don't deliver to localhost or private addresses, and we don't follow redirects.
2. In the portal open **Webhooks**, add `https://<your host>/webhooks/isbridgeup` and pick
   `status.changed` and `source.outage`.
3. Copy the signing secret it shows you (once) into `IBU_WEBHOOK_SECRET` and restart.
4. Press **Send test event**: the service logs `queued status.changed …` and ignores it, because a
   test event carries no bridge.

## What the code does, and why

These are the rules from [Keep state in sync](https://dash.isbridgeup.com/docs/#sync):

- **Start from a snapshot**, then apply webhooks. Read the snapshot again after a restart.
- **Apply a state only if its revision is the same or newer** than the one held. `revision` is
  `state_changed_at` in milliseconds, so a REST item has one too.
- **Unknown is held until a snapshot shows a state.** REST `null`, webhook `UNKNOWN`, a value the
  client doesn't know, or a `degraded` outage that names the bridge: the bridge shows as unknown,
  and a later event only triggers a re-read.
- **Read the Live API again after every `source.outage` event.** A bridge that leaves a degraded
  list gets no `recovered` event of its own, and `recovered` never means lowered.
- **Deduplicate on the event `id`**, kept for 3 days (automatic retries last about 24 hours).
- **Verify, persist, then answer.** The receiver checks the signature on the raw bytes, writes the
  event to the inbox in the state file and answers `204`; the worker applies it afterwards.
  Nothing slow happens before the answer.
- **Count minutes on the API's clock** (`server_time`), not your own.
- **A snapshot lists every bridge your key can see.** A bridge it no longer lists (your plan or
  scope changed) is forgotten and shows as unknown, never kept at its old state.
- **Keep two ages apart.** How old a bridge's state is belongs to us: while the API gives a state,
  it vouches for it, however old `state_changed_at` or `observed_at` is. How long ago the Live
  API last answered you belongs to you: the service keeps that time in the state file. When the
  API refuses the key (`401`/`403`), every route turns CHECK at once; when reads fail (timeouts,
  `5xx`, a long `429`) for longer than `IBU_MAX_OFFLINE_SECONDS`, the same. The next successful
  read brings the board back. A restart with an old state file shows CHECK until a read succeeds.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `IBU_KEY` | | Your server key. Keep it out of code and shell history. |
| `IBU_API_BASE` | `https://api.isbridgeup.com` | |
| `IBU_ROUTES` | `../routes.example.json` | Your routes and their bridge ids. |
| `IBU_DB` | `bridge-sync.sqlite3` (Python), `bridge-sync.json` (Node) | The state file. |
| `IBU_WEBHOOK_SECRET` | | The endpoint's signing secret (`whsec_…`). Starts the receiver. |
| `IBU_PORT` | `8080` | Receiver port. |
| `IBU_WEBHOOK_PATH` | `/webhooks/isbridgeup` | Receiver path. |
| `IBU_POLL_SECONDS` | the API's `ttl_seconds`, at least 30 | Safety-net interval. |
| `IBU_MAX_OFFLINE_SECONDS` | 3 × the poll interval | Without a successful Live API read for this long, every route shows CHECK. |

Run one bridge-sync per account and share its state with your drivers, robots and dispatch
tools. Reading every 30 seconds for the whole 7-day trial is 20,160 requests, which fits the
trial's 25,000; one poller per vehicle would not. Public screens are different: each needs its
own device token (see [Displays](https://dash.isbridgeup.com/docs/#displays)).

## Before you run it in production

This is a reference, small enough to read in one sitting. Things to change for production:

- **One process.** To run several instances, keep the state and the inbox in your shared
  database, and let one worker apply events (or take a lock per bridge).
- **The board is printed.** Replace the print with your dispatch tool, message bus or cache.
- **The state file** is SQLite (Python) or a JSON file written atomically (Node). Back it up, or
  move the small `Store` interface onto your own database.
- **HTTPS ends at your proxy or tunnel**; the receiver itself speaks plain HTTP.
- **Logs** never contain the key or the signing secret. Keep it that way, and never send either
  to us: the key prefix or the `X-IsBridgeUp-Delivery` id is enough to trace a request.

## Tests

```bash
cd python && python3 -m unittest discover -s tests -t .
cd node && npm test
```

The fixtures are checked against the IsBridgeUp API on every release: responses validate
against the v1 schemas, and every event is exactly what the server builds.

## Links

- [Developer docs](https://dash.isbridgeup.com/docs/) and the
  [API reference](https://api.isbridgeup.com/api/dash/docs)
- [API Terms](https://dash.isbridgeup.com/terms/)
- Questions or a security report: support@isbridgeup.com

## License

[MIT](LICENSE)
