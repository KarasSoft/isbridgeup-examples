// The dispatcher board: each of your routes, and the bridges on it.
//
// A route is BLOCKED when one of its bridges is raised, CHECK when we can't
// vouch for one of them, and CLEAR only when every bridge on it is lowered.
// Raise durations are history ("past raises here"), never a countdown.

const RANK = { CLEAR: 0, UNKNOWN: 1, UP: 2 }
const ROUTE_STATUS = { CLEAR: 'CLEAR', UNKNOWN: 'CHECK', UP: 'BLOCKED' }

// A bridge we have nothing for is unknown, not lowered.
export const bridgeState = (states, id) => states.bridges.get(id) ?? { state: 'UNKNOWN', revision: null, hold: true }

export function routeStatus(states, bridgeIds) {
  let worst = 'CLEAR'
  for (const id of bridgeIds) {
    const { state } = bridgeState(states, id)
    const known = state in RANK ? state : 'UNKNOWN'
    if (RANK[known] > RANK[worst]) worst = known
  }
  return ROUTE_STATUS[worst]
}

export function bridgeLine(name, b, duration, nowMs) {
  if (b.state !== 'UP' && b.state !== 'CLEAR') return `${name}: unknown, we can't vouch for it right now`
  const since = b.revision == null ? '' : ` for ${Math.max(0, Math.floor((nowMs - b.revision) / 60000))} min`
  if (b.state === 'CLEAR') return `${name}: lowered${since}`
  let typical = ''
  if (duration && duration.median_minutes != null) {
    typical = ` (past raises here: median ${duration.median_minutes} min`
    if (duration.p90_minutes != null) typical += `, 9 in 10 within ${duration.p90_minutes} min`
    typical += ')'
  }
  return `${name}: raised${since}${typical}`
}

export function render(routes, states, names, durations, nowMs) {
  const lines = []
  for (const route of routes) {
    lines.push(`${routeStatus(states, route.bridges).padEnd(8)} ${route.name}`)
    for (const id of route.bridges) {
      lines.push(`  ${bridgeLine(names.get(id) ?? id, bridgeState(states, id), durations.get(id), nowMs)}`)
    }
  }
  return lines.join('\n')
}
