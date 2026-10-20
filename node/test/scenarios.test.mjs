// Every scenario in fixtures/scenarios/ ends in the state it promises.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { readFailed } from '../bridge-sync.mjs'
import { load, steps } from '../src/scenarios.mjs'
import { normalizeState, revisionOf } from '../src/state.mjs'
import { Store } from '../src/store.mjs'
import { Sync } from '../src/sync.mjs'
import { FIXTURES, ROUTES, SCENARIOS, fixture } from './helpers.mjs'

// Play a scenario. Returns the Sync, what each event asked for, and the
// scenario's last moment (route statuses are taken then).
export function run(path) {
  const sync = new Sync(ROUTES, new Store(':memory:'), { maxOfflineS: load(path).max_offline_seconds ?? null })
  const reread = []
  let lastMs = 0
  for (const { kind, body, atMs } of steps(path)) {
    lastMs = atMs
    if (kind === 'snapshot') sync.applySnapshot(body, { setClock: false })
    else if (kind === 'event') reread.push(sync.applyEvent(body))
    else if (kind === 'api_error') readFailed(sync, body.status, body.code)
  }
  return { sync, reread, atMs: lastMs }
}

test('every scenario ends as described', () => {
  assert.ok(SCENARIOS.length >= 10)
  for (const path of SCENARIOS) {
    const { expect } = load(path)
    const { sync, reread, atMs } = run(path)
    assert.deepEqual(Object.fromEntries(sync.states.bridges), expect.bridges, path)
    assert.deepEqual(reread, expect.reread, path)
    assert.deepEqual(sync.routeStatuses(atMs), expect.routes, path)
  }
})

test('the board matches the sample', () => {
  const { sync } = run(SCENARIOS[0])
  const expected = readFileSync(join(FIXTURES, 'board-01.txt'), 'utf8').replace(/\n$/, '')
  assert.equal(sync.board(revisionOf(fixture('live.json').server_time)), expected)
})

test('unknown values are never lowered', () => {
  for (const value of [null, undefined, 'UNKNOWN', 'DOWN', 'clear', '', 0, 'SOMETHING_NEW']) {
    assert.equal(normalizeState(value), 'UNKNOWN')
  }
  assert.equal(normalizeState('CLEAR'), 'CLEAR')
})

test('revision is state_changed_at in ms', () => {
  const { data } = fixture('events/brickell-raised.json')
  assert.equal(revisionOf(data.state_changed_at), data.revision)
  assert.equal(revisionOf('2026-10-16T13:58:40Z'), data.revision)
})
