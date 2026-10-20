// The command-line service: key checks, replay, how it backs off, and the
// whole thing as a process against a local stand-in for the API.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { explain, keyProblem, refresh, replay } from '../bridge-sync.mjs'
import { sign } from '../send-event.mjs'
import { ApiError } from '../src/client.mjs'
import { Store } from '../src/store.mjs'
import { Sync } from '../src/sync.mjs'
import { ROUTES, SCENARIOS, SECRET, fixture } from './helpers.mjs'

const NODE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')

function quietly(fn) {
  const original = console.log
  const out = []
  console.log = (...args) => out.push(args.join(' '))
  try {
    return [fn(), out.join('\n')]
  } finally {
    console.log = original
  }
}

test('key checks', () => {
  assert.match(keyProblem(''), /IBU_KEY/)
  assert.match(keyProblem('whsec_abc'), /signing secret/)
  assert.match(keyProblem('ibu_dev_abc'), /display token/)
  assert.equal(keyProblem('ibu_live_abc'), null)
})

test('replay runs every scenario', () => {
  for (const path of SCENARIOS) {
    const [code, out] = quietly(() => replay(path))
    assert.equal(code, 0)
    assert.match(out, /== snapshot live\.json/)
  }
})

test('refresh backs off', async () => {
  const sync = new Sync(ROUTES, new Store(':memory:'))
  const client = (outcome) => ({ live: async () => { if (outcome instanceof Error) throw outcome; return outcome } })
  const original = console.log
  console.log = () => {}
  try {
    assert.equal(await refresh(sync, client(fixture('live.json'))), null)
    assert.equal(await refresh(sync, client(new ApiError(429, 'rate_limited', '', 12))), 12_000)
    assert.equal(await refresh(sync, client(new ApiError(403, 'trial_expired', ''))), 300_000)
    assert.equal(await refresh(sync, client(new ApiError(503, null, ''))), 60_000)
    assert.equal(await refresh(sync, client(new TypeError('fetch failed'))), 30_000)
  } finally {
    console.log = original
  }
  assert.equal(sync.states.bridges.size, 3)
})

test('api errors are explained', () => {
  assert.match(explain(new ApiError(403, 'scope_required', '')), /Integrator/)
})

const freePort = () =>
  new Promise((resolve) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => {
      const { port } = s.address()
      s.close(() => resolve(port))
    })
  })

test('as a process: snapshot, webhooks, re-read, stop', async () => {
  let liveReads = 0
  const api = http.createServer((req, res) => {
    const body = req.url === '/v1/bridges' ? fixture('catalog.json') : req.url === '/v1/bridges/live' ? (liveReads++, fixture('live.json')) : null
    res.writeHead(body ? 200 : 404, { 'Content-Type': 'application/json' })
    res.end(body ? JSON.stringify(body) : '')
  })
  await new Promise((resolve) => api.listen(0, '127.0.0.1', resolve))
  const dir = mkdtempSync(join(tmpdir(), 'ibu-'))
  const port = await freePort()
  const child = spawn(process.execPath, ['bridge-sync.mjs'], {
    cwd: NODE_DIR,
    env: {
      ...process.env,
      IBU_KEY: 'ibu_live_example',
      IBU_API_BASE: `http://127.0.0.1:${api.address().port}`,
      IBU_DB: join(dir, 'state.json'),
      IBU_WEBHOOK_SECRET: SECRET,
      IBU_PORT: String(port),
      IBU_POLL_SECONDS: '300',
    },
  })
  let output = ''
  child.stdout.on('data', (chunk) => (output += chunk))
  child.stderr.on('data', (chunk) => (output += chunk))
  const waitFor = async (check, what) => {
    const deadline = Date.now() + 10_000
    while (!check()) {
      if (Date.now() > deadline) assert.fail(`never saw ${what}; output:\n${output}`)
      await new Promise((r) => setTimeout(r, 50))
    }
  }
  const deliver = async (name) => {
    const body = Buffer.from(JSON.stringify(fixture(name)))
    const res = await fetch(`http://127.0.0.1:${port}/webhooks/isbridgeup`, {
      method: 'POST',
      body,
      headers: { 'Content-Type': 'application/json', 'X-IsBridgeUp-Signature': sign(SECRET, body, Math.floor(Date.now() / 1000)) },
    })
    return res.status
  }
  try {
    await waitFor(() => output.includes('receiving webhooks'), 'the receiver start')
    assert.match(output, /BLOCKED {2}Brickell loop/)
    const reads = liveReads

    assert.equal(await deliver('events/brickell-lowered.json'), 204)
    await waitFor(() => output.includes('CLEAR    Brickell loop'), 'the lowered board')
    assert.equal(liveReads, reads) // a plain change needs no re-read

    assert.equal(await deliver('events/outage-degraded.json'), 204)
    await waitFor(() => liveReads > reads, 'a re-read after the outage event')

    const exited = new Promise((resolve) => child.on('exit', resolve))
    child.kill('SIGTERM')
    assert.equal(await exited, 0)
  } finally {
    child.kill('SIGKILL')
    api.closeAllConnections()
    api.close()
    rmSync(dir, { recursive: true, force: true })
  }
})
