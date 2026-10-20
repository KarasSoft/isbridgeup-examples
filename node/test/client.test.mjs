// The REST client against a local stand-in for the API: the key header, the
// error shapes, Retry-After.
import assert from 'node:assert/strict'
import http from 'node:http'
import { after, before, test } from 'node:test'
import { ApiError, Client, USER_AGENT, errorFrom } from '../src/client.mjs'
import { fixture } from './helpers.mjs'

const ANSWERS = {
  '/v1/bridges/live': [200, {}, fixture('live.json')],
  '/v1/bridges': [403, {}, { detail: { error: 'trial_expired', message: 'Your 7-day trial has ended.' } }],
}
const seen = []
let server
let client

before(async () => {
  server = http.createServer((req, res) => {
    seen.push([req.url, req.headers['x-api-key'], req.headers['user-agent']])
    const [status, headers, body] = ANSWERS[req.url] ?? [429, { 'Retry-After': '17' }, { detail: { error: 'rate_limited', message: 'Slow down' } }]
    res.writeHead(status, { ...headers, 'Content-Type': 'application/json' })
    res.end(JSON.stringify(body))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  client = new Client('ibu_live_example', `http://127.0.0.1:${server.address().port}/`, 5000)
})
after(() => {
  server.closeAllConnections()
  server.close()
})

test('live sends the key and returns the snapshot', async () => {
  assert.deepEqual(await client.live(), fixture('live.json'))
  assert.deepEqual(seen.find(([url]) => url === '/v1/bridges/live'), ['/v1/bridges/live', 'ibu_live_example', USER_AGENT])
})

test('errors carry the code', async () => {
  await assert.rejects(client.bridges(), (err) => err instanceof ApiError && err.status === 403 && err.code === 'trial_expired')
})

test('a rate limit carries Retry-After', async () => {
  await assert.rejects(client.get('/v1/anything'), (err) => err.code === 'rate_limited' && err.retryAfter === 17)
})

test('every error shape', () => {
  assert.equal(errorFrom(403, { detail: { error: 'scope_required', message: 'm' } }).code, 'scope_required')
  const plain = errorFrom(401, { detail: 'Authentication required (API key or bearer token)' })
  assert.deepEqual([plain.code, plain.text], [null, 'Authentication required (API key or bearer token)'])
  const listed = errorFrom(422, { detail: [{ loc: ['query', 'days'], msg: 'Input should be a valid integer' }] })
  assert.deepEqual([listed.code, listed.text], ['invalid_request', 'Input should be a valid integer'])
  assert.equal(errorFrom(502, null).code, null)
  assert.equal(errorFrom(404, { detail: { error: 'bridge_not_found' } }).text, '')
})
