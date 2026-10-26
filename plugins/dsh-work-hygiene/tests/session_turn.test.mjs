import test from 'node:test'
import assert from 'node:assert/strict'
import { collectTurnEvents, handleTurnEnd } from '../lib/session_turn.js'
import { normalizeConfig } from '../lib/config.js'

const EVENTS = [
  { type: 'turn/start', seq: 1, data: { turn: 1 } },
  { type: 'user/message', seq: 2, data: { content: '请算 1+1' } },
  { type: 'assistant/message', seq: 3, data: { message: { content: [{ type: 'text', text: '1+1=2' }] } } },
  { type: 'turn/end', seq: 4, data: { turn: 1 } },
  { type: 'turn/start', seq: 5, data: { turn: 2 } },
  { type: 'user/message', seq: 6, data: { content: '你好' } },
  { type: 'assistant/message', seq: 7, data: { message: { content: [{ type: 'text', text: '你好' }] } } },
  { type: 'turn/end', seq: 8, data: { turn: 2 } },
]
const session = { ownEvents: () => EVENTS }

test('session: collect only requested turn', () => {
  const t1 = collectTurnEvents(session, 1)
  assert.equal(t1.length, 4)
  assert.equal(t1[0].type, 'turn/start')
  assert.equal(t1[t1.length - 1].type, 'turn/end')
  const t2 = collectTurnEvents(session, 2)
  assert.equal(t2.length, 4)
  assert.equal(t2[0].data.turn, 2)
})

test('session: unknown turn is empty', () => {
  assert.deepEqual(collectTurnEvents(session, 99), [])
})

test('session: handleTurnEnd returns calc decisions', () => {
  const r = handleTurnEnd(session, { type: 'turn/end', data: { turn: 1 } }, normalizeConfig())
  assert.equal(r.turn, 1)
  assert.equal(r.decisions.length >= 1, true)
  assert.equal(r.decisions[0].module, 'calc')
})

test('session: non turn/end is ignored', () => {
  assert.equal(handleTurnEnd(session, { type: 'assistant/message' }, normalizeConfig()), null)
})

test('session: calc disabled returns no decisions', () => {
  const r = handleTurnEnd(session, { type: 'turn/end', data: { turn: 1 } },
    normalizeConfig({ features: { calc: { enabled: false } } }))
  assert.deepEqual(r.decisions, [])
})

test('session: command plans returned', () => {
  const r = handleTurnEnd(session, { type: 'turn/end', data: { turn: 1 } },
    normalizeConfig({ features: { layout: { command: 'python check.py' } } }))
  assert.deepEqual(r.commands.map(c => c.feature), ['layout'])
})

test('session: safe on missing session/events', () => {
  assert.deepEqual(collectTurnEvents(null, 1), [])
  assert.equal(handleTurnEnd({ ownEvents: () => undefined }, { type: 'turn/end', data: { turn: 1 } }, normalizeConfig()).turn, 1)
})
