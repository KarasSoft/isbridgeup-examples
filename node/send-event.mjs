#!/usr/bin/env node
// Send a fixture event to your local receiver, signed the way IsBridgeUp signs
// deliveries. Try duplicates, late events and bad signatures without waiting
// for a real bridge.
//
//   export IBU_WEBHOOK_SECRET=whsec_local_test   # the same value the receiver uses
//   node send-event.mjs ../fixtures/events/brickell-lowered.json
//   node send-event.mjs ../fixtures/events/brickell-lowered.json --times 2      # a retry: applied once
//   node send-event.mjs ../fixtures/events/brickell-raised.json                 # older: ignored
//   node send-event.mjs ../fixtures/events/brickell-lowered.json --age 600     # stale: 401
import crypto from 'node:crypto'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

export function sign(secret, body, t) {
  const digest = crypto.createHmac('sha256', secret).update(`${t}.`).update(body).digest('hex')
  return `t=${t},v1=${digest}`
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      url: { type: 'string', default: 'http://localhost:8080/webhooks/isbridgeup' },
      times: { type: 'string', default: '1' },
      'new-id': { type: 'boolean', default: false },
      age: { type: 'string', default: '0' },
    },
  })
  const secret = process.env.IBU_WEBHOOK_SECRET ?? ''
  if (!secret || positionals.length !== 1) {
    console.error('usage: IBU_WEBHOOK_SECRET=… node send-event.mjs EVENT.json [--url URL] [--times N] [--new-id] [--age SECONDS]')
    return 2
  }
  const event = JSON.parse(readFileSync(positionals[0], 'utf8'))
  if (values['new-id']) event.id = crypto.randomUUID()
  const body = Buffer.from(JSON.stringify(event))
  const delivery = crypto.randomUUID() // the same for every retry of this delivery

  for (let attempt = 1; attempt <= Number(values.times); attempt++) {
    const t = Math.floor(Date.now() / 1000) - Number(values.age) // a new t per attempt
    let status
    try {
      const res = await fetch(values.url, {
        method: 'POST',
        body,
        headers: {
          'Content-Type': 'application/json',
          'X-IsBridgeUp-Event': String(event.type),
          'X-IsBridgeUp-Delivery': delivery,
          'X-IsBridgeUp-Signature': sign(secret, body, t),
        },
        signal: AbortSignal.timeout(5000),
      })
      status = res.status
    } catch (err) {
      console.error(`Can't reach ${values.url}: ${err.cause?.code ?? err.name}`)
      return 1
    }
    console.log(`attempt ${attempt}: ${event.type} ${event.id} → HTTP ${status}`)
  }
  return 0
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main()
}
