import crypto from 'node:crypto'

// True only for an authentic, fresh delivery. Never throws on bad input.
//   secret:  the endpoint's signing secret (whsec_…), whole, not your API key
//   rawBody: the request body as a Buffer or Uint8Array, exactly as received (before JSON parsing)
//   header:  the X-IsBridgeUp-Signature header, e.g. "t=1792159121,v1=5f2a…"
export function verify(secret, rawBody, header, tolerance = 300) {
  if (typeof secret !== 'string' || secret === '') return false
  if (typeof header !== 'string' || header === '' || !(rawBody instanceof Uint8Array)) return false
  const parts = {}
  for (const item of header.split(',')) {
    const i = item.indexOf('=')
    if (i > 0) parts[item.slice(0, i).trim()] = item.slice(i + 1).trim()
  }
  const t = parts.t ?? ''
  const v1 = parts.v1 ?? ''
  if (!/^[0-9]{1,12}$/.test(t) || !/^[0-9a-f]{64}$/.test(v1)) return false
  // Too old or from the future: possible replay.
  if (Math.abs(Date.now() / 1000 - Number(t)) > tolerance) return false
  const expected = crypto
    .createHmac('sha256', secret) // the whole secret, whsec_ included: nothing to decode
    .update(Buffer.concat([Buffer.from(`${t}.`), rawBody]))
    .digest()
  return crypto.timingSafeEqual(expected, Buffer.from(v1, 'hex'))
}
