// One durable view of your bridges, fed by Live API snapshots and webhooks.
import { render, routeStatus } from './board.mjs'
import { BridgeStates, revisionOf } from './state.mjs'

export class Sync {
  constructor(routes, store) {
    this.routes = routes
    this.store = store
    this.states = new BridgeStates()
    store.load(this.states)
    this.names = new Map()
    this.durations = new Map()
    this.offsetMs = 0 // the API's clock minus ours, from the last snapshot
  }

  nowMs() {
    return Date.now() + this.offsetMs
  }

  // Names from GET /v1/bridges. Returns the route bridge ids the key can't see.
  learnCatalog(catalog) {
    const visible = new Set()
    for (const b of catalog.bridges ?? []) {
      this.names.set(b.id, b.name ?? b.id)
      visible.add(b.id)
    }
    const wanted = new Set(this.routes.flatMap((r) => r.bridges))
    return [...wanted].filter((id) => !visible.has(id)).sort()
  }

  applySnapshot(snapshot, { setClock = true } = {}) {
    // Minutes are counted on the API's clock, not ours (ours may be off).
    if (setClock && snapshot.server_time) this.offsetMs = revisionOf(snapshot.server_time) - Date.now()
    for (const b of snapshot.bridges ?? []) {
      if (!this.names.has(b.id)) this.names.set(b.id, b.name ?? b.id)
      if ('raise_duration' in b) this.durations.set(b.id, b.raise_duration) // only on plans that include it
    }
    this.states.applySnapshot(snapshot)
    this.store.save(this.states)
  }

  // Apply one verified event. True when the Live API should be read again.
  applyEvent(event, { seq = null } = {}) {
    const reread = this.states.applyEvent(event)
    this.store.save(this.states, { doneSeq: seq, eventId: event.id })
    return reread
  }

  // Apply every queued delivery in arrival order. True if any asks for a re-read.
  drainInbox() {
    let reread = false
    for (let item = this.store.nextQueued(); item; item = this.store.nextQueued()) {
      reread = this.applyEvent(JSON.parse(item.body), { seq: item.seq }) || reread
    }
    return reread
  }

  fingerprint() {
    return JSON.stringify([...this.states.bridges].map(([id, b]) => [id, b.state]).sort())
  }

  routeStatuses() {
    return Object.fromEntries(this.routes.map((r) => [r.name, routeStatus(this.states, r.bridges)]))
  }

  board(nowMs = this.nowMs()) {
    return render(this.routes, this.states, this.names, this.durations, nowMs)
  }
}
