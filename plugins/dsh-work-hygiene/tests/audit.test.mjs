import test from 'node:test'
import assert from 'node:assert/strict'
import { createAuditRecord, toAuditLine } from '../lib/audit.js'

test('audit: records rules and effects without command text', () => {
  const r = createAuditRecord({
    operation: { kind: 'command', toolName: 'pwsh', targets: [], command: 'Remove-Item 定稿/x' },
    decision: { kind: 'deny', reason: 'protected' },
    downstream: 'allow',
    reasons: [{ module: 'commands', rule: 'destructive-command', level: 'deny', reason: 'protected' }],
    effects: [{ kind: 'snapshot', target: 'a.txt' }],
  })
  assert.equal(r.plugin, 'dsh-work-hygiene')
  assert.equal(r.level, 'deny')
  assert.equal(r.tool, 'pwsh')
  assert.equal(r.operation, 'command')
  assert.deepEqual(r.effects, ['snapshot'])
  assert.equal(r.rules.length, 1)
  assert.equal(r.rules[0].rule, 'destructive-command')
  assert.equal(JSON.stringify(r).includes('Remove-Item'), false)
  assert.equal(JSON.stringify(r).includes('定稿/x'), false)
})

test('audit: JSONL is valid and one line', () => {
  const line = toAuditLine({
    operation: { kind: 'file-write', toolName: 'write', targets: ['a.txt'] },
    decision: { kind: 'allow' },
    downstream: 'allow',
    reasons: [],
    effects: [],
  })
  assert.equal(line.endsWith('\n'), false)
  assert.equal(line.includes('\n'), false)
  const parsed = JSON.parse(line)
  assert.equal(parsed.level, 'allow')
  assert.equal(parsed.operation, 'file-write')
})

test('audit: safe on empty input', () => {
  const r = createAuditRecord({})
  assert.equal(r.plugin, 'dsh-work-hygiene')
  assert.equal(r.level, 'allow')
  assert.deepEqual(r.rules, [])
})
