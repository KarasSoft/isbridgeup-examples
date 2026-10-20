// REST sends "UP" | "CLEAR" | null; webhooks send "UP" | "CLEAR" | "UNKNOWN".
// Anything else is unknown too: never read an unexpected value as CLEAR.
export const normalizeState = (value) => (value === 'UP' || value === 'CLEAR' ? value : 'UNKNOWN')

// A REST item's revision: state_changed_at in epoch milliseconds, the same
// number a status.changed webhook carries for that change.
export const revisionOf = (stateChangedAt) => (stateChangedAt ? Date.parse(stateChangedAt) : null)

// The latest state per bridge, merged from webhooks and REST snapshots.
// In production keep `bridges` and `seen` in your database, so a restart
// neither forgets state nor applies an event twice.
export class BridgeStates {
  bridges = new Map() // bridgeId -> { state: 'UP' | 'CLEAR' | 'UNKNOWN', revision: number | null, hold: boolean }
  seen = new Map() // event id -> when we handled it (epoch ms)

  #apply(bridgeId, state, revision) {
    const held = this.bridges.get(bridgeId)?.revision ?? null
    if (revision == null || (held != null && revision < held)) return // order unknown, or older
    this.bridges.set(bridgeId, { state, revision, hold: false })
  }

  // We can't vouch for this bridge. Show unknown, remember the newest
  // revision seen, and wait for a snapshot that shows a state again.
  #holdUnknown(bridgeId, revision = null) {
    const known = [this.bridges.get(bridgeId)?.revision ?? null, revision].filter((r) => r != null)
    this.bridges.set(bridgeId, { state: 'UNKNOWN', revision: known.length ? Math.max(...known) : null, hold: true })
  }

  // GET /v1/bridges/live: at start-up, when applyEvent asks for it, and about
  // every ttl_seconds as a safety net.
  applySnapshot(snapshot) {
    for (const b of snapshot.bridges) {
      const revision = revisionOf(b.state_changed_at)
      if (b.state == null) this.#holdUnknown(b.id, revision)
      else this.#apply(b.id, normalizeState(b.state), revision)
    }
  }

  // A verified webhook. Returns true when you should re-read the Live API and
  // pass the result to applySnapshot.
  applyEvent(event) {
    if (this.seen.has(event.id)) return false // duplicate delivery
    this.seen.set(event.id, Date.now())
    const data = event.data ?? {}
    if (data.test) return false // the portal's test event carries no bridge
    if (event.type === 'status.changed') {
      const state = normalizeState(data.state)
      if (state === 'UNKNOWN' || this.bridges.get(data.bridge_id)?.hold) {
        // Only a snapshot lifts an unknown: a feed can go quiet without a new
        // event, so a retried event can be older news than it.
        this.#holdUnknown(data.bridge_id, data.revision ?? null)
        return true
      }
      this.#apply(data.bridge_id, state, data.revision ?? null)
    } else if (event.type === 'source.outage') {
      // Each event lists every bridge of yours still affected. One that drops
      // off the list gets no "recovered" event, and "recovered" never means
      // CLEAR: re-read after any outage event.
      if (data.status === 'degraded') for (const id of data.bridge_ids ?? []) this.#holdUnknown(id)
      return true
    }
    return false
  }

  // Forget handled event ids older than your retry window.
  prune(maxAgeMs = 3 * 24 * 3600 * 1000) {
    const cutoff = Date.now() - maxAgeMs
    for (const [id, at] of this.seen) if (at < cutoff) this.seen.delete(id)
  }
}
