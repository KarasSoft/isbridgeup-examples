// The receiver over real HTTP: verify, persist, answer; the worker applies once.
import assert from 'node:assert/strict'
import http from 'node:http'
import { afterEach, beforeEach, test } from 'node:test'
import { sign } from '../send-event.mjs'
import { MAX_BODY, makeServer } from '../src/receiver.mjs'
import { Store } from '../src/store.mjs'
import { Sync } from '../src/sync.mjs'
import { BRICKELL, ROUTES, SECRET, fixture } from './helpers.mjs'

const PATH = '/webhooks/isbridgeup'
let store
let sync
let server
let base
let queued

beforeEach(async () => {
  store = new Store(':memory:')
  sync = new Sync(ROUTES, store)
  sync.applySnapshot(fixture('live.json'), { setClock: false })
  queued = 0
  server = makeServer({ path: PATH, secret: SECRET, store, onQueued: () => queued++ })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${server.address().port}`
})
afterEach(async () => {
  server.closeAllConnections()
  await new Promise((resolve) => server.close(resolve))
})

const now = () => Math.floor(Date.now() / 1000)

async function post(body, signature, path = PATH) {
  const headers = { 'Content-Type': 'application/json' }
  if (signature) headers['X-IsBridgeUp-Signature'] = signature
  const res = await fetch(base + path, { method: 'POST', body, headers })
  return res.status
}

// A raw request whose Content-Length we choose (fetch always sets the real one).
function postDeclaring(length) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${PATH}`, { method: 'POST', headers: { 'Content-Length': length } }, (res) => {
      res.resume()
      resolve(res.statusCode)
    })
    req.on('error', reject)
    req.end()
  })
}

test('a signed delivery is queued, then applied once', async () => {
  const body = Buffer.from(JSON.stringify(fixture('events/brickell-lowered.json')))
  assert.equal(await post(body, sign(SECRET, body, now())), 204)
  assert.equal(queued, 1)
  assert.equal(await post(body, sign(SECRET, body, now())), 204) // a retry
  assert.equal(store.queued(), 1)
  assert.equal(sync.drainInbox(), false)
  assert.equal(sync.states.bridges.get(BRICKELL).state, 'CLEAR')
  assert.equal(await post(body, sign(SECRET, body, now())), 204) // after it was applied
  assert.equal(sync.drainInbox(), false)
  assert.equal(store.queued(), 0)
})

test('rejected deliveries change nothing', async () => {
  const body = Buffer.from(JSON.stringify(fixture('events/brickell-lowered.json')))
  const t = now()
  assert.equal(await post(body, sign('whsec_wrong', body, t)), 401)
  assert.equal(await post(body, sign(SECRET, body, t - 3600)), 401)
  assert.equal(await post(body), 401)
  assert.equal(await post(body, sign(SECRET, body, t), '/other'), 404)
  const notJson = Buffer.from('not json')
  assert.equal(await post(notJson, sign(SECRET, notJson, t)), 400)
  const noId = Buffer.from('{"type": "status.changed"}')
  assert.equal(await post(noId, sign(SECRET, noId, t)), 400)
  assert.equal(await postDeclaring(MAX_BODY + 1), 413)
  assert.equal(store.queued(), 0)
})

test('health', async () => {
  const res = await fetch(`${base}/healthz`)
  assert.equal(await res.text(), 'ok\n')
})
