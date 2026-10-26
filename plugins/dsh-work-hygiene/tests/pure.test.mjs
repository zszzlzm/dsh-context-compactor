import test from 'node:test'
import assert from 'node:assert/strict'
import {
  LEVELS,
  mergeDecisions,
  detectCalc,
  detectCommands,
  planSnapshot,
  planRecovery,
  evaluateCommandCheck,
} from '../lib/pure.js'

const levels = ds => ds.map(d => d.level)

test('calc: arithmetic is detected as warn', () => {
  const d = detectCalc('37×4.8 = 177.6')
  assert.equal(d.length, 1)
  assert.equal(d[0].module, 'calc')
  assert.equal(d[0].level, 'warn')
  assert.ok(Array.isArray(d[0].matches))
})

test('calc: percent and aggregate are detected', () => {
  assert.ok(detectCalc('增长 12.5%').length >= 1)
  assert.ok(detectCalc('平均 22.5 字 / 块').length >= 1)
})

test('calc: unit conversion is detected', () => {
  assert.ok(detectCalc('1.5 GB 是多少 MB').length >= 1)
})

test('calc: date duration is detected', () => {
  assert.ok(detectCalc('10:30 到 12:15 多久').length >= 1)
})

test('calc: version, port and path are not flagged', () => {
  assert.deepEqual(detectCalc('v1.2.3 修复；端口 11434；src/_试作/a.py'), [])
})

test('calc: code block is not treated as natural-language math', () => {
  assert.deepEqual(detectCalc('```js\nconst x = a + b\n```'), [])
})

test('calc: used num.py tool suppresses warning', () => {
  assert.deepEqual(detectCalc('37×4.8 = 177.6', { usedNumTool: true }), [])
})

test('commands: protected Remove-Item path denies in deny mode', () => {
  const d = detectCommands('Remove-Item -Recurse 定稿/x', {
    mode: 'deny', protectedPaths: ['定稿'],
  })
  assert.equal(d.length, 1)
  assert.equal(d[0].module, 'commands')
  assert.equal(d[0].level, 'deny')
})

test('commands: harmless rm temp.txt passes', () => {
  assert.deepEqual(detectCommands('rm temp.txt', { protectedPaths: ['定稿'] }), [])
})

test('commands: unknown command passes', () => {
  assert.deepEqual(detectCommands('Get-ChildItem .', {}), [])
})

test('merge: deny > ask > warn > allow', () => {
  assert.equal(LEVELS.deny > LEVELS.ask, true)
  assert.equal(mergeDecisions([
    { module: 'a', rule: 'r', level: 'warn', reason: 'w' },
    { module: 'b', rule: 'r', level: 'deny', reason: 'd' },
    { module: 'c', rule: 'r', level: 'ask', reason: 'a' },
  ]).level, 'deny')
})

test('merge: collects reasons and defaults to allow', () => {
  assert.equal(mergeDecisions([]).level, 'allow')
  const m = mergeDecisions([
    { module: 'calc', rule: 'arithmetic', level: 'warn', reason: 'w1' },
    { module: 'calc', rule: 'percent', level: 'warn', reason: 'w2' },
  ])
  assert.equal(m.level, 'warn')
  assert.equal(m.reasons.length, 2)
})

test('snapshot: plans only destructive file operations when enabled', () => {
  const op = { kind: 'file-write', targets: ['a.txt'] }
  assert.deepEqual(planSnapshot(op, { snapshot: { enabled: false } }), [])
  const effects = planSnapshot(op, { snapshot: { enabled: true } })
  assert.equal(effects.length, 1)
  assert.equal(effects[0].kind, 'snapshot')
  assert.equal(effects[0].target, 'a.txt')
})

test('recovery: plans backup for destructive operations when enabled', () => {
  const op = { kind: 'file-delete', targets: ['a.txt'] }
  assert.deepEqual(planRecovery(op, { recovery: { enabled: false } }), [])
  const effects = planRecovery(op, { recovery: { enabled: true, backupCommand: 'backup.cmd' } })
  assert.equal(effects.length, 1)
  assert.equal(effects[0].kind, 'backup')
  assert.equal(effects[0].command, 'backup.cmd')
})

test('command check: exit 0 passes, nonzero warns', () => {
  assert.deepEqual(evaluateCommandCheck({ exitCode: 0, stdout: 'ok' }, { name: 'tests' }), [])
  const d = evaluateCommandCheck({ exitCode: 1, stderr: 'bad' }, { name: 'tests' })
  assert.equal(d.length, 1)
  assert.equal(d[0].level, 'warn')
  assert.equal(d[0].module, 'checks')
})
