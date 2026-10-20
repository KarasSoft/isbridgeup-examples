// A restart loses nothing: states, handled ids and queued deliveries survive.
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, test } from 'node:test'
import { Store } from '../src/store.mjs'
import { Sync } from '../src/sync.mjs'
import { BRICKELL, ROUTES, fixture } from './helpers.mjs'

let dir
let path
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'ibu-'))
  path = join(dir, 'state.json')
})
afterEach(() => rmSync(dir, { recursive: true, force: true }))

test('state and handled ids survive a restart', () => {
  const lowered = fixture('events/brickell-lowered.json')
  let sync = new Sync(ROUTES, new Store(path))
  sync.applySnapshot(fixture('live.json'), { setClock: false })
  sync.applyEvent(lowered)
  const before = Object.fromEntries(sync.states.bridges)

  sync = new Sync(ROUTES, new Store(path))
  assert.deepEqual(Object.fromEntries(sync.states.bridges), before)
  assert.ok(sync.states.seen.has(lowered.id))
  assert.equal(sync.applyEvent(lowered), false) // a retry after the restart: ignored
  assert.deepEqual(Object.fromEntries(sync.states.bridges), before)
})

test('queued deliveries are applied after a restart', () => {
  const lowered = fixture('events/brickell-lowered.json')
  let store = new Store(path)
  new Sync(ROUTES, store).applySnapshot(fixture('live.json'), { setClock: false })
  assert.equal(store.enqueue(lowered.id, JSON.stringify(lowered)), true)
  assert.equal(store.enqueue(lowered.id, JSON.stringify(lowered)), false) // already queued
  // stopped before the worker ran

  store = new Store(path)
  const sync = new Sync(ROUTES, store)
  assert.equal(store.queued(), 1)
  assert.equal(sync.drainInbox(), false)
  assert.equal(store.queued(), 0)
  assert.equal(sync.states.bridges.get(BRICKELL).state, 'CLEAR')
})

test('old handled ids are pruned', () => {
  const store = new Store(path)
  const sync = new Sync(ROUTES, store)
  sync.applyEvent(fixture('events/test-event.json')) // handled now
  sync.states.seen.set('handled-ten-days-ago', Date.now() - 10 * 864e5)
  store.save(sync.states, { eventId: 'handled-ten-days-ago' })
  store.prune(sync.states, 3 * 864e5)
  assert.equal(sync.states.seen.has('handled-ten-days-ago'), false)
  assert.equal(sync.states.seen.size, 1)
  assert.equal(new Sync(ROUTES, new Store(path)).states.seen.size, 1)
})
