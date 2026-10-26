import test from 'node:test'
import assert from 'node:assert/strict'
import { createSessionHandler } from '../lib/session_handler.js'
import { normalizeConfig } from '../lib/config.js'

const events = [
  { type: 'turn/start', seq: 1, data: { turn: 1 } },
  { type: 'user/message', seq: 2, data: { content: '请算 1+1' } },
  { type: 'assistant/message', seq: 3, data: { message: { content: [{ type: 'text', text: '1+1=2' }] } } },
  { type: 'turn/end', seq: 4, data: { turn: 1 } },
]
const session = { ownEvents: () => events }

test('session_handler: turn/end returns decisions and logs', () => {
  const logs = []
  const h = createSessionHandler({ config: normalizeConfig(), logger: { info: (...a) => logs.push(a) } })
  const r = h(session, { type: 'turn/end', data: { turn: 1 } })
  assert.equal(r.turn, 1)
  assert.equal(r.decisions.length >= 1, true)
  assert.ok(logs.length >= 1)
})

test('session_handler: non turn/end is null', () => {
  const h = createSessionHandler({ config: normalizeConfig() })
  assert.equal(h(session, { type: 'assistant/message' }), null)
})

test('session_handler: exceptions are logged, not thrown', () => {
  const warnings = []
  const h = createSessionHandler({
    config: normalizeConfig(),
    logger: { info: () => {}, warn: (...a) => warnings.push(a) },
  })
  const badEvent = { get type() { throw new Error('boom') } }
  const r = h(session, badEvent)
  assert.equal(r, null)
  assert.ok(warnings.length >= 1)
})
