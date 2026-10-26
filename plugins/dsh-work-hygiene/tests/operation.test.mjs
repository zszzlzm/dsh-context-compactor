import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeOperation, isDestructiveKind } from '../lib/operation.js'

test('operation: read/write/edit/delete/move/command', () => {
  assert.equal(normalizeOperation('read', { file_path: 'a.txt' }).kind, 'file-read')
  assert.equal(normalizeOperation('write', { file_path: 'a.txt' }).kind, 'file-write')
  assert.equal(normalizeOperation('edit', { file_path: 'a.txt' }).kind, 'file-write')
  assert.equal(normalizeOperation('delete', { path: 'a.txt' }).kind, 'file-delete')
  const mv = normalizeOperation('move', { source: 'a.txt', destination: 'b.txt' })
  assert.equal(mv.kind, 'file-move')
  assert.deepEqual(mv.targets, ['a.txt', 'b.txt'])
  const cmd = normalizeOperation('pwsh', { command: 'Get-ChildItem .' })
  assert.equal(cmd.kind, 'command')
  assert.equal(cmd.command, 'Get-ChildItem .')
})

test('operation: safe on null/unknown and destructive predicate', () => {
  const op = normalizeOperation(null, null)
  assert.equal(op.kind, 'unknown')
  assert.deepEqual(op.targets, [])
  assert.equal(isDestructiveKind('file-write'), true)
  assert.equal(isDestructiveKind('file-read'), false)
})
