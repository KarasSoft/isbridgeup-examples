// A minimal IsBridgeUp REST client: built-in fetch, a timeout on every call,
// and errors you can branch on.

export const DEFAULT_BASE = 'https://api.isbridgeup.com'
export const USER_AGENT = 'isbridgeup-examples/1.0 (node)'

// A non-2xx answer. Branch on `code` (detail.error), never on the text.
export class ApiError extends Error {
  constructor(status, code, text, retryAfter = null) {
    super(`HTTP ${status}${code ? ` ${code}` : ''}${text ? `: ${text}` : ''}`)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.text = text
    this.retryAfter = retryAfter
  }
}

// Errors share one shape, {"detail": {"error": …, "message": …}}, but a few
// answers differ: a plain-string detail (no X-API-Key header, a server error)
// and a list of problems (an invalid query parameter). Check before reading.
export function errorFrom(status, body, retryAfter = null) {
  const detail = body && typeof body === 'object' ? body.detail : undefined
  if (Array.isArray(detail)) {
    const problems = detail.map((p) => (p && typeof p === 'object' ? (p.msg ?? JSON.stringify(p)) : String(p)))
    return new ApiError(status, 'invalid_request', problems.join('; '), retryAfter)
  }
  if (detail && typeof detail === 'object') return new ApiError(status, detail.error ?? null, detail.message ?? '', retryAfter)
  return new ApiError(status, null, typeof detail === 'string' ? detail : '', retryAfter)
}

// GET /v1/bridges and GET /v1/bridges/live with a server key.
export class Client {
  constructor(key, base = DEFAULT_BASE, timeoutMs = 10_000) {
    this.key = key
    this.base = base.replace(/\/+$/, '')
    this.timeoutMs = timeoutMs
  }

  bridges() {
    return this.get('/v1/bridges')
  }

  live() {
    return this.get('/v1/bridges/live')
  }

  async get(path) {
    const res = await fetch(this.base + path, {
      headers: { 'X-API-Key': this.key, Accept: 'application/json', 'User-Agent': USER_AGENT },
      signal: AbortSignal.timeout(this.timeoutMs),
    })
    const text = await res.text()
    let body = null
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      body = null
    }
    if (!res.ok) {
      const retryAfter = res.headers.get('retry-after') ?? ''
      throw errorFrom(res.status, body, /^\d+$/.test(retryAfter) ? Number(retryAfter) : null)
    }
    if (body === null) throw new SyntaxError('the API answered with something other than JSON')
    return body
  }
}
