// One durable view of your bridges, fed by Live API snapshots and webhooks.
//
// Two ages are kept apart. How old a bridge's state is belongs to IsBridgeUp:
// while the API gives a state, it vouches for it, however old. How long ago the
// Live API last answered belongs to you: when it hasn't for too long, or it
// refused the key (401/403), every bridge is shown unknown until a read succeeds.
import { render, routeStatus } from './board.mjs'
import { BridgeStates, revisionOf } from './state.mjs'

export const DEFAULT_MAX_OFFLINE_S = 90 // bridge-sync.mjs uses 3 × its poll interval

const NOTHING = new BridgeStates() // the view while we can't vouch: every bridge unknown

export class Sync {
  // maxOfflineS: how long without a successful Live API read before every
  // bridge shows as unknown. null turns the rule off (replays).
  constructor(routes, store, { maxOfflineS = DEFAULT_MAX_OFFLINE_S } = {}) {
    this.routes = routes
    this.store = store
    this.states = new BridgeStates()
    store.load(this.states)
    // When the Live API last answered (on its clock), and the error code it
    // refused the key with, until a read succeeds again.
    const { lastSyncMs, accessLost } = store.loadLink()
    this.lastSyncMs = lastSyncMs
    this.accessLost = accessLost
    this.maxOfflineS = maxOfflineS
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
    const readAt = revisionOf(snapshot.server_time) ?? this.nowMs()
    this.lastSyncMs = Math.max(readAt, this.lastSyncMs ?? 0)
    this.accessLost = null
    this.store.save(this.states)
    this.store.saveLink(this.lastSyncMs, this.accessLost)
  }

  // The Live API refused the key (401/403): stop vouching right away.
  loseAccess(code) {
    this.accessLost = code
    this.store.saveLink(this.lastSyncMs, this.accessLost)
  }

  // Why we can't vouch for any bridge right now, or null.
  linkProblem(nowMs = this.nowMs()) {
    if (this.accessLost) return `IsBridgeUp refused the key (${this.accessLost})`
    if (this.lastSyncMs == null) return 'no answer from IsBridgeUp yet'
    if (this.maxOfflineS != null && nowMs - this.lastSyncMs > this.maxOfflineS * 1000) {
      const minutes = Math.floor((nowMs - this.lastSyncMs) / 60000)
      const last = new Date(this.lastSyncMs).toISOString().slice(11, 16)
      return `no answer from IsBridgeUp for ${minutes} min (last read ${last} UTC)`
    }
    return null
  }

  #view(nowMs) {
    return this.linkProblem(nowMs) ? NOTHING : this.states
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

  // What the board shows, without the minutes: print again when it changes.
  fingerprint(nowMs = this.nowMs()) {
    const view = this.#view(nowMs)
    return JSON.stringify([this.linkProblem(nowMs) == null, [...view.bridges].map(([id, b]) => [id, b.state]).sort()])
  }

  // CLEAR / CHECK / BLOCKED per route: what to hand your dispatch tools.
  routeStatuses(nowMs = this.nowMs()) {
    const view = this.#view(nowMs)
    return Object.fromEntries(this.routes.map((r) => [r.name, routeStatus(view, r.bridges)]))
  }

  board(nowMs = this.nowMs()) {
    const problem = this.linkProblem(nowMs)
    const board = render(this.routes, problem ? NOTHING : this.states, this.names, this.durations, nowMs)
    return problem ? `NO LINK  ${problem}: every bridge is unknown until a read succeeds\n${board}` : board
  }
}
