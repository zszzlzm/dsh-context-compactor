import test from 'node:test'
import assert from 'node:assert/strict'
import { arbitrate } from '../lib/arbiter.js'
import { normalizeConfig } from '../lib/config.js'

test('arbiter: allow plans snapshot when enabled', () => {
  const op = { kind: 'file-write', targets: ['a.txt'] }
  const cfg = normalizeConfig({ features: { snapshot: { enabled: true } } })
  const r = arbitrate(op, cfg, [])
  assert.equal(r.level, 'allow')
  assert.equal(r.effects.length, 1)
  assert.equal(r.effects[0].kind, 'snapshot')
})

test('arbiter: deny wins and suppresses effects', () => {
  const op = { kind: 'file-write', targets: ['a.txt'] }
  const cfg = normalizeConfig({ features: { snapshot: { enabled: true } } })
  const r = arbitrate(op, cfg, [
    () => [{ module: 'x', rule: 'r', level: 'warn', reason: 'w' }],
    () => [{ module: 'y', rule: 'r', level: 'deny', reason: 'd' }],
  ])
  assert.equal(r.level, 'deny')
  assert.deepEqual(r.effects, [])
})

test('arbiter: evaluator throw fails closed', () => {
  const r = arbitrate({ kind: 'command', targets: [], command: 'rm x' }, normalizeConfig(), [
    () => { throw new Error('boom') },
  ])
  assert.equal(r.level, 'deny')
  assert.equal(r.reasons[0].module, 'evaluator')
})
