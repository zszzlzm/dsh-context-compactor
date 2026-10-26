import test from 'node:test'
import assert from 'node:assert/strict'
import { createPreExecuteHandler } from '../lib/host_preexecute.js'
import { normalizeConfig } from '../lib/config.js'

const allowNext = async () => ({ kind: 'allow' })
const denyNext = async () => ({ kind: 'deny', reason: 'downstream' })
const askNext = async () => ({ kind: 'ask', reason: 'downstream' })
const cancelNext = async () => ({ kind: 'cancel' })
const exec = (name = 'pwsh', args = { command: 'echo hi' }) => ({ name, arguments: args })

test('host: warn lets through and audits', async () => {
  const h = createPreExecuteHandler({
    config: normalizeConfig(),
    evaluators: [() => [{ module: 'calc', rule: 'arithmetic', level: 'warn', reason: 'w' }]],
  })
  const r = await h(exec(), allowNext)
  assert.equal(r.decision.kind, 'allow')
  assert.equal(r.audit.length, 1)
  assert.equal(r.audit[0].rule, 'arithmetic')
})

test('host: internal deny wins and suppresses effects', async () => {
  const h = createPreExecuteHandler({
    config: normalizeConfig({ features: { snapshot: { enabled: true } } }),
    evaluators: [() => [{ module: 'x', rule: 'r', level: 'deny', reason: 'd' }]],
  })
  const r = await h(exec('write', { file_path: 'a.txt' }), allowNext)
  assert.equal(r.decision.kind, 'deny')
  assert.deepEqual(r.effects, [])
})

test('host: downstream deny wins over internal allow', async () => {
  const h = createPreExecuteHandler({ config: normalizeConfig(), evaluators: [] })
  const r = await h(exec(), denyNext)
  assert.equal(r.decision.kind, 'deny')
})

test('host: downstream ask + internal deny => deny', async () => {
  const h = createPreExecuteHandler({
    config: normalizeConfig(),
    evaluators: [() => [{ module: 'x', rule: 'r', level: 'deny', reason: 'd' }]],
  })
  const r = await h(exec(), askNext)
  assert.equal(r.decision.kind, 'deny')
})

test('host: downstream ask alone becomes ask', async () => {
  const h = createPreExecuteHandler({ config: normalizeConfig(), evaluators: [] })
  const r = await h(exec(), askNext)
  assert.equal(r.decision.kind, 'ask')
})

test('host: downstream cancel is terminal and has no effects', async () => {
  const h = createPreExecuteHandler({
    config: normalizeConfig({ features: { snapshot: { enabled: true } } }),
    evaluators: [],
  })
  const r = await h(exec('write', { file_path: 'a.txt' }), cancelNext)
  assert.equal(r.decision.kind, 'cancel')
  assert.deepEqual(r.effects, [])
})

test('host: snapshot effect is planned for allowed destructive op', async () => {
  const h = createPreExecuteHandler({
    config: normalizeConfig({ features: { snapshot: { enabled: true } } }),
    evaluators: [],
  })
  const r = await h(exec('write', { file_path: 'a.txt' }), allowNext)
  assert.equal(r.decision.kind, 'allow')
  assert.equal(r.effects.length, 1)
  assert.equal(r.effects[0].kind, 'snapshot')
})

test('host: evaluator sees normalized operation command', async () => {
  let seen = null
  const h = createPreExecuteHandler({
    config: normalizeConfig(),
    evaluators: [(op) => { seen = op; return [] }],
  })
  await h(exec('pwsh', { command: 'Get-ChildItem .' }), allowNext)
  assert.equal(seen.kind, 'command')
  assert.equal(seen.command, 'Get-ChildItem .')
})

test('host: next throw fails closed', async () => {
  const h = createPreExecuteHandler({ config: normalizeConfig(), evaluators: [] })
  const r = await h(exec(), async () => { throw new Error('next boom') })
  assert.equal(r.decision.kind, 'deny')
})
