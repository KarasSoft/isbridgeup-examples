// Read the scenarios in fixtures/scenarios/: the replay mode and the tests use them.
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { revisionOf } from './state.mjs'

export const load = (path) => JSON.parse(readFileSync(path, 'utf8'))

// [{ kind, body, label, atMs }] per step, atMs being the moment the step
// happens (for the board's minutes and the time since the last read):
//   snapshot   a Live API answer (file name or inline)
//   event      a verified webhook (file name or inline)
//   api_error  a failed Live API read: { "status": 403, "code": "…", "at": "<ISO time>" }
//   clock      time passes: "<ISO time>"
export function steps(path) {
  const fixtures = dirname(dirname(resolve(path)))
  let atMs = 0
  return load(path).steps.map((step) => {
    const [[kind, ref]] = Object.entries(step)
    let body, label, moment
    if (kind === 'clock') {
      ;[body, label, moment] = [ref, `at ${ref}`, ref]
    } else if (kind === 'api_error') {
      ;[body, label, moment] = [ref, `HTTP ${ref.status} ${ref.code ?? ''}`.trimEnd(), ref.at]
    } else {
      body = typeof ref === 'string' ? load(join(fixtures, ref)) : ref
      label = typeof ref === 'string' ? ref : '(inline)'
      moment = kind === 'snapshot' ? body.server_time : body.created_at
    }
    if (moment) atMs = Math.max(atMs, revisionOf(moment))
    return { kind, body, label, atMs }
  })
}
