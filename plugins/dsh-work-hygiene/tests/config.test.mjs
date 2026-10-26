import test from 'node:test'
import assert from 'node:assert/strict'
import { DEFAULT_CONFIG, normalizeConfig } from '../lib/config.js'

test('config: defaults only enable unique features', () => {
  assert.equal(DEFAULT_CONFIG.features.calc.enabled, true)
  assert.equal(DEFAULT_CONFIG.features.layout.enabled, true)
  assert.equal(DEFAULT_CONFIG.features.issues.enabled, true)
  assert.equal(DEFAULT_CONFIG.features.repro.enabled, true)
  assert.equal(DEFAULT_CONFIG.features.claims.enabled, false)
  assert.equal(DEFAULT_CONFIG.features.tests.enabled, false)
  assert.equal(DEFAULT_CONFIG.features.recovery.enabled, false)
  assert.equal(DEFAULT_CONFIG.features.commands.enabled, false)
})

test('config: overrides and normalization', () => {
  const c = normalizeConfig({
    enabled: false,
    features: {
      calc: { enabled: false },
      commands: { enabled: true, mode: 'deny', protectedPaths: ['定稿'] },
    },
  })
  assert.equal(c.enabled, false)
  assert.equal(c.features.calc.enabled, false)
  assert.equal(c.features.commands.enabled, true)
  assert.equal(c.features.commands.mode, 'deny')
  assert.deepEqual(c.features.commands.protectedPaths, ['定稿'])
  assert.equal(c.features.claims.enabled, false)
})
