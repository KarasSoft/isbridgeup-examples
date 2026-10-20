// Durable state in one JSON file: the state per bridge, the event ids already
// handled, and the webhook inbox. Every change is written to a temporary file
// and renamed over the old one, so a crash never leaves half a file.
//
// The inbox is what makes it safe to answer a delivery quickly: the receiver
// writes the verified event here, answers 2xx, and the worker applies it
// afterwards. If the process stops in between, the event is still in the
// inbox on the next start.
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs'

const empty = () => ({ version: 1, bridges: {}, seen: {}, inbox: [], nextSeq: 1 })

export class Store {
  constructor(path) {
    this.path = path
    this.data = path !== ':memory:' && existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : empty()
  }

  // Fill a BridgeStates with what was saved before a restart.
  load(states) {
    for (const [id, b] of Object.entries(this.data.bridges)) states.bridges.set(id, { ...b })
    for (const [id, at] of Object.entries(this.data.seen)) states.seen.set(id, at)
  }

  // Persist a verified delivery. False when that event is already queued.
  enqueue(eventId, body) {
    if (this.data.inbox.some((item) => item.eventId === eventId)) return false
    this.data.inbox.push({ seq: this.data.nextSeq++, eventId, body, receivedAt: Date.now() })
    this.#flush()
    return true
  }

  // { seq, body } of the oldest queued event, or null.
  nextQueued() {
    return this.data.inbox[0] ?? null
  }

  queued() {
    return this.data.inbox.length
  }

  // In one write: every bridge, the event id just handled, and the removal of
  // the inbox entry it came from.
  save(states, { doneSeq = null, eventId = null } = {}) {
    this.data.bridges = Object.fromEntries([...states.bridges].map(([id, b]) => [id, { ...b }]))
    if (eventId != null && states.seen.has(eventId)) this.data.seen[eventId] = states.seen.get(eventId)
    if (doneSeq != null) this.data.inbox = this.data.inbox.filter((item) => item.seq !== doneSeq)
    this.#flush()
  }

  // Forget handled event ids older than your retry window.
  prune(states, maxAgeMs) {
    states.prune(maxAgeMs)
    const cutoff = Date.now() - maxAgeMs
    for (const [id, at] of Object.entries(this.data.seen)) if (at < cutoff) delete this.data.seen[id]
    this.#flush()
  }

  #flush() {
    if (this.path === ':memory:') return
    const tmp = `${this.path}.tmp`
    writeFileSync(tmp, JSON.stringify(this.data))
    renameSync(tmp, this.path)
  }
}
