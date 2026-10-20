#!/usr/bin/env node
// bridge-sync: keep your dispatch tools in step with IsBridgeUp bridge states.
//
// It reads the Live API at start-up, takes status.changed webhooks as they
// come, reads the Live API again when an event asks for it and about every
// ttl_seconds as a safety net, keeps everything in one JSON file so a restart
// loses nothing, and prints a board of your routes whenever a bridge changes.
//
//   node bridge-sync.mjs --replay ../fixtures/scenarios/10-snapshot-after-recovery.json
//   node bridge-sync.mjs --once     # needs IBU_KEY: one snapshot, print the board, exit
//   node bridge-sync.mjs            # needs IBU_KEY; IBU_WEBHOOK_SECRET also starts the receiver
//
// Settings (environment variables):
//   IBU_KEY             your server key (ibu_live_…). Keep it out of code and history.
//   IBU_API_BASE        default https://api.isbridgeup.com
//   IBU_ROUTES          your routes file, default ../routes.example.json
//   IBU_DB              the state file, default ./bridge-sync.json
//   IBU_WEBHOOK_SECRET  the endpoint's signing secret (whsec_…); starts the receiver
//   IBU_PORT            receiver port, default 8080
//   IBU_WEBHOOK_PATH    receiver path, default /webhooks/isbridgeup
//   IBU_POLL_SECONDS    safety-net interval, default the API's ttl_seconds (at least 30)
//   IBU_MAX_OFFLINE_SECONDS  without a successful Live API read for this long, every
//                       bridge shows as unknown (CHECK); default 3 × the poll interval
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { ApiError, Client, DEFAULT_BASE } from './src/client.mjs'
import { makeServer } from './src/receiver.mjs'
import { load, steps } from './src/scenarios.mjs'
import { Store } from './src/store.mjs'
import { Sync } from './src/sync.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SEEN_IDS_KEPT_MS = 3 * 24 * 3600 * 1000 // longer than the ~24 h of automatic retries
const REREAD_MIN_GAP_MS = 5000 // a burst of events causes one re-read, not many

const HINTS = {
  invalid_api_key: 'check IBU_KEY: the whole key, not revoked (portal → API Keys)',
  scope_required: 'your plan has no Live API: it comes with Integrator and the trial',
  trial_expired: 'choose a plan in the portal (Billing); the key works again right away',
  subscription_ended: 'resume a plan in the portal (Billing); nothing was deleted',
  rate_limited: 'too many requests a minute; waiting for Retry-After',
  volume_exceeded: 'the request pool is used up for this period',
}

export const log = (line) => console.log(new Date().toTimeString().slice(0, 8), line)

export function explain(err) {
  const hint = HINTS[err.code ?? '']
  return `API answered ${err.message}${hint ? `: ${hint}` : ''}`
}

export function keyProblem(key) {
  if (!key) return 'Set IBU_KEY to your server key (portal → API Keys), or try --replay without one.'
  if (key.startsWith('whsec_')) return 'IBU_KEY holds a signing secret. Use your server key there; the secret goes in IBU_WEBHOOK_SECRET.'
  if (key.startsWith('ibu_dev_')) return 'IBU_KEY holds a display token. bridge-sync needs a server key (ibu_live_…).'
  return null
}

export function loadRoutes() {
  const path = process.env.IBU_ROUTES || join(HERE, '..', 'routes.example.json')
  return { routes: JSON.parse(readFileSync(path, 'utf8')).routes, path }
}

// Run a fixtures scenario: no key, no network.
export function replay(path) {
  const scenario = load(path)
  const sync = new Sync(loadRoutes().routes, new Store(':memory:'), { maxOfflineS: scenario.max_offline_seconds ?? null })
  console.log(scenario.description)
  for (const { kind, body, label, atMs } of steps(path)) {
    let note = ''
    let shown = label
    if (kind === 'snapshot') {
      sync.applySnapshot(body, { setClock: false })
    } else if (kind === 'event') {
      if (sync.applyEvent(body)) note = '  → read the Live API again'
      shown = `${label} (${body.type})`
    } else if (kind === 'api_error') {
      note = `  → ${readFailed(sync, body.status, body.code)}`
    }
    console.log(`\n== ${kind} ${shown}${note}`)
    console.log(sync.board(atMs))
  }
  return 0
}

// Read a snapshot. Returns how long to wait before the next try (ms), or null
// for the normal interval.
// A refused key is shown at once; anything else only once the Live API has
// been silent for longer than maxOfflineS.
export function readFailed(sync, status, code) {
  if (status === 401 || status === 403) {
    sync.loseAccess(code ?? `HTTP ${status}`)
    return 'every bridge is unknown until a read succeeds'
  }
  return 'the board holds for now; too long without an answer turns it to CHECK'
}

export async function refresh(sync, client) {
  try {
    sync.applySnapshot(await client.live())
    return null
  } catch (err) {
    if (err instanceof ApiError) {
      log(explain(err))
      readFailed(sync, err.status, err.code)
      if (err.status === 429) return (err.retryAfter ?? 60) * 1000
      return err.status === 401 || err.status === 403 ? 300_000 : 60_000
    }
    // network trouble, timeouts, a garbled answer
    log(`couldn't read the Live API (${err.name}); trying again in 30 s`)
    return 30_000
  }
}

class Waker {
  #pending = false
  #resolve = null
  wake() {
    if (this.#resolve) this.#resolve()
    else this.#pending = true
  }
  wait(ms) {
    if (this.#pending) {
      this.#pending = false
      return Promise.resolve()
    }
    return new Promise((resolve) => {
      const timer = setTimeout(done, ms)
      const self = this
      function done() {
        clearTimeout(timer)
        self.#resolve = null
        resolve()
      }
      this.#resolve = done
    })
  }
}

export async function run({ once = false } = {}) {
  const key = (process.env.IBU_KEY ?? '').trim()
  const problem = keyProblem(key)
  if (problem) {
    console.error(problem)
    return 2
  }
  const { routes, path: routesPath } = loadRoutes()
  const client = new Client(key, process.env.IBU_API_BASE || DEFAULT_BASE)
  const store = new Store(process.env.IBU_DB || join(HERE, 'bridge-sync.json'))
  const sync = new Sync(routes, store)

  let snapshot
  try {
    const missing = sync.learnCatalog(await client.bridges())
    snapshot = await client.live()
    if (missing.length) log(`not visible to this key, check ${routesPath}: ${missing.join(', ')}`)
  } catch (err) {
    if (err instanceof ApiError) readFailed(sync, err.status, err.code) // an old cache stays unknown for anyone reading the store
    console.error(err instanceof ApiError ? explain(err) : `Can't reach ${client.base} (${err.cause?.code ?? err.name}).`)
    return 1
  }
  sync.applySnapshot(snapshot)
  console.log(sync.board())
  if (once) return 0

  const pollMs = 1000 * Number(process.env.IBU_POLL_SECONDS || Math.max(30, Number(snapshot.ttl_seconds) || 30))
  sync.maxOfflineS = Number(process.env.IBU_MAX_OFFLINE_SECONDS || (3 * pollMs) / 1000)
  const waker = new Waker()
  let stopped = false
  let server = null
  const secret = (process.env.IBU_WEBHOOK_SECRET ?? '').trim()
  if (secret) {
    const port = Number(process.env.IBU_PORT || 8080)
    const path = process.env.IBU_WEBHOOK_PATH || '/webhooks/isbridgeup'
    server = makeServer({ path, secret, store, onQueued: () => waker.wake(), log })
    await new Promise((resolve) => server.listen(port, resolve))
    log(`receiving webhooks on http://localhost:${port}${path}: register its public HTTPS URL in the portal`)
  } else {
    log(`IBU_WEBHOOK_SECRET is not set: snapshots only, every ${pollMs / 1000} s`)
  }
  const shutdown = () => {
    stopped = true
    waker.wake()
  }
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)

  let nextPoll = Date.now() + pollMs
  let lastReread = 0
  let nextPrune = 0
  let shown = sync.fingerprint()
  if (store.queued()) waker.wake() // deliveries persisted before a restart
  while (!stopped) {
    await waker.wait(Math.max(50, Math.min(1000, nextPoll - Date.now())))
    const wanted = sync.drainInbox()
    const now = Date.now()
    if ((wanted && now - lastReread >= REREAD_MIN_GAP_MS) || now >= nextPoll) {
      const delay = await refresh(sync, client)
      lastReread = Date.now()
      nextPoll = lastReread + (delay ?? pollMs)
    } else if (wanted) {
      nextPoll = Math.min(nextPoll, lastReread + REREAD_MIN_GAP_MS)
    }
    if (now >= nextPrune) {
      store.prune(sync.states, SEEN_IDS_KEPT_MS)
      nextPrune = now + 3600_000
    }
    const current = sync.fingerprint()
    if (current !== shown) {
      console.log(sync.board())
      shown = current
    }
  }
  if (server) {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  }
  return 0
}

async function main(argv) {
  const replayAt = argv.indexOf('--replay')
  if (replayAt !== -1) {
    if (!argv[replayAt + 1]) {
      console.error('usage: node bridge-sync.mjs --replay ../fixtures/scenarios/<name>.json')
      return 2
    }
    return replay(argv[replayAt + 1])
  }
  return run({ once: argv.includes('--once') })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2))
}
