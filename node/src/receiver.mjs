// The webhook endpoint: verify, persist, answer. Nothing slow happens before
// the answer; the worker in bridge-sync.mjs applies queued events afterwards.
import http from 'node:http'
import { verify } from './verify.mjs'

export const MAX_BODY = 256 * 1024 // deliveries are a few hundred bytes

export function makeServer({ path, secret, store, onQueued = () => {}, log = () => {} }) {
  return http.createServer((req, res) => {
    const reply = (status, text = '') => {
      if (res.headersSent) return
      res.writeHead(status, text ? { 'Content-Type': 'text/plain; charset=utf-8' } : {})
      res.end(text)
    }
    const url = (req.url ?? '').split('?')[0]
    if (req.method === 'GET' && url === '/healthz') return reply(200, 'ok\n')
    if (url !== path) return reply(404)
    if (req.method !== 'POST') return reply(405)
    if (Number(req.headers['content-length']) > MAX_BODY) {
      res.setHeader('Connection', 'close')
      return reply(413)
    }

    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY) {
        res.setHeader('Connection', 'close')
        reply(413)
        req.destroy()
      } else {
        chunks.push(chunk)
      }
    })
    req.on('end', () => {
      if (res.headersSent) return
      const raw = Buffer.concat(chunks) // the exact bytes we signed
      if (!verify(secret, raw, req.headers['x-isbridgeup-signature'])) {
        log('rejected a delivery: bad, stale or missing signature')
        return reply(401) // a real delivery is retried
      }
      let event
      try {
        event = JSON.parse(raw.toString('utf8'))
      } catch {
        return reply(400)
      }
      if (!event || typeof event !== 'object' || event.id == null || !event.type) return reply(400)
      const fresh = store.enqueue(String(event.id), raw.toString('utf8'))
      reply(204) // persisted, so it is safe to acknowledge now
      log(`${fresh ? 'queued' : 'already queued'} ${event.type} ${event.id} (delivery ${req.headers['x-isbridgeup-delivery'] ?? '-'})`)
      onQueued()
    })
  })
}
