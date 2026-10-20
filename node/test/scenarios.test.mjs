// Every scenario in fixtures/scenarios/ ends in the state it promises.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { load, steps } from '../src/scenarios.mjs'
import { normalizeState, revisionOf } from '../src/state.mjs'
import { Store } from '../src/store.mjs'
import { Sync } from '../src/sync.mjs'
import { FIXTURES, ROUTES, SCENARIOS, fixture } from './helpers.mjs'

export function run(path) {
  const sync = new Sync(ROUTES, new Store(':memory:'))
  const reread = []
  for (const { kind, body } of steps(path)) {
    if (kind === 'snapshot') sync.applySnapshot(body, { setClock: false })
    else reread.push(sync.applyEvent(body))
  }
  return { sync, reread }
}

test('every scenario ends as described', () => {
  assert.ok(SCENARIOS.length >= 10)
  for (const path of SCENARIOS) {
    const { expect } = load(path)
    const { sync, reread } = run(path)
    assert.deepEqual(Object.fromEntries(sync.states.bridges), expect.bridges, path)
    assert.deepEqual(reread, expect.reread, path)
    assert.deepEqual(sync.routeStatuses(), expect.routes, path)
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
