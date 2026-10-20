// verify() accepts what IsBridgeUp signs and rejects everything else, without throwing.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sign } from '../send-event.mjs'
import { verify } from '../src/verify.mjs'
import { SECRET, fixture } from './helpers.mjs'

const BODY = Buffer.from(JSON.stringify(fixture('events/brickell-lowered.json')))
const now = () => Math.floor(Date.now() / 1000)

test('a valid delivery', () => {
  assert.equal(verify(SECRET, BODY, sign(SECRET, BODY, now())), true)
  assert.equal(verify(SECRET, new Uint8Array(BODY), sign(SECRET, BODY, now())), true) // fetch-style handlers
})

test('rejections', () => {
  const t = now()
  const good = sign(SECRET, BODY, t)
  const sig = good.split('v1=')[1]
  const cases = {
    'wrong secret': ['whsec_other', BODY, good],
    'secret without its prefix': [SECRET.slice('whsec_'.length), BODY, good],
    'tampered body': [SECRET, Buffer.from(BODY.toString().replace('CLEAR', 'UP')), good],
    're-serialized body': [SECRET, Buffer.from(JSON.stringify(JSON.parse(BODY), null, 2)), good],
    stale: [SECRET, BODY, sign(SECRET, BODY, t - 3600)],
    'from the future': [SECRET, BODY, sign(SECRET, BODY, t + 3600)],
    'missing header': [SECRET, BODY, undefined],
    'empty header': [SECRET, BODY, ''],
    garbage: [SECRET, BODY, 'garbage'],
    'no timestamp': [SECRET, BODY, `v1=${sig}`],
    'no signature': [SECRET, BODY, `t=${t}`],
    'short signature': [SECRET, BODY, `t=${t},v1=00`],
    'upper-case hex': [SECRET, BODY, `t=${t},v1=${sig.toUpperCase()}`],
    'non-numeric t': [SECRET, BODY, `t=abc,v1=${sig}`],
    'huge t': [SECRET, BODY, `t=${'9'.repeat(400)},v1=${sig}`],
    'parsed body': [SECRET, JSON.parse(BODY), good],
    'string body': [SECRET, BODY.toString(), good],
    'header array': [SECRET, BODY, [good]],
    'no secret': [undefined, BODY, good],
    'empty secret': ['', BODY, good],
  }
  for (const [name, [secret, body, header]] of Object.entries(cases)) {
    assert.equal(verify(secret, body, header), false, name)
  }
})
