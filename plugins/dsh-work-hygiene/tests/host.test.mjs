import test from 'node:test'
import assert from 'node:assert/strict'
import { apply, name, inject } from '../index.js'

function fakeCtx() {
  const handlers = {}
  const sections = []
  const logs = []
  return {
    handlers,
    sections,
    logs,
    effects: [],
    tools: { guard: () => () => {} },
    systemPrompt: { section: (s) => { sections.push(s); return () => {} } },
    logger: { info: (...args) => logs.push(args) },
    effect(fn) { const d = fn(); this.effects.push(d); return d || (() => {}) },
    on(event, fn) { handlers[event] = fn; return () => {} },
  }
}

test('host: exports plugin identity and injects tools/systemPrompt', () => {
  assert.equal(name, '@local/dsh-work-hygiene')
  assert.deepEqual(inject, ['tools', 'systemPrompt'])
})

test('host: registers one pre-execute listener and a prompt section', () => {
  const ctx = fakeCtx()
  apply(ctx, {})
  assert.equal(typeof ctx.handlers['tools/pre-execute'], 'function')
  assert.equal(ctx.sections.length, 1)
})

test('host: command deny through registered listener', async () => {
  const ctx = fakeCtx()
  apply(ctx, {
    features: { commands: { enabled: true, mode: 'deny', protectedPaths: ['定稿'] } },
  })
  const decision = await ctx.handlers['tools/pre-execute'](
    { name: 'pwsh', arguments: { command: 'Remove-Item 定稿/x' } },
    async () => ({ kind: 'allow' }),
  )
  assert.equal(decision.kind, 'deny')
  assert.ok(ctx.logs.length >= 1)
})

test('host: disabled registers nothing', () => {
  const ctx = fakeCtx()
  apply(ctx, { enabled: false })
  assert.equal(ctx.handlers['tools/pre-execute'], undefined)
  assert.equal(ctx.sections.length, 0)
})
