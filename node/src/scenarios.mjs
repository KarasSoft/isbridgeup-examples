// Read the scenarios in fixtures/scenarios/: the replay mode and the tests use them.
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { revisionOf } from './state.mjs'

export const load = (path) => JSON.parse(readFileSync(path, 'utf8'))

// [{ kind, body, label, atMs }] per step: kind is "snapshot" or "event",
// atMs the moment the step happens (for the board's minutes).
export function steps(path) {
  const fixtures = dirname(dirname(resolve(path)))
  let atMs = 0
  return load(path).steps.map((step) => {
    const [[kind, ref]] = Object.entries(step)
    const body = typeof ref === 'string' ? load(join(fixtures, ref)) : ref
    const moment = kind === 'snapshot' ? body.server_time : body.created_at
    if (moment) atMs = Math.max(atMs, revisionOf(moment))
    return { kind, body, label: typeof ref === 'string' ? ref : '(inline)', atMs }
  })
}
