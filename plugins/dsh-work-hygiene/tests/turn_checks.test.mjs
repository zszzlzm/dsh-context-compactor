import test from 'node:test'
import assert from 'node:assert/strict'
import { extractTurnText, hasNumToolCall, checkTurn, planTurnCommands } from '../lib/turn_checks.js'
import { normalizeConfig } from '../lib/config.js'

const turnEvents = ({ user = '请计算 37×4.8', answer = '37×4.8 = 177.6', numTool = false } = {}) => {
  const events = [
    { type: 'turn/start', data: { turn: 1 } },
    { type: 'user/message', data: { content: user } },
  ]
  if (numTool) {
    events.push({ type: 'tool/call', data: { name: 'pwsh', arguments: JSON.stringify({ command: 'python src/num.py eval "37*4.8"' }) } })
    events.push({ type: 'tool/result', data: { message: { isError: false, content: [{ type: 'text', text: '177.6' }] } } })
  }
  events.push({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: answer }] } } })
  events.push({ type: 'turn/end', data: { turn: 1 } })
  return events
}

test('turn: extract user and assistant text', () => {
  const t = extractTurnText(turnEvents())
  assert.equal(t.userText.includes('37×4.8'), true)
  assert.equal(t.assistantText.includes('177.6'), true)
})

test('turn: calc warning when no num.py call', () => {
  const d = checkTurn(turnEvents(), normalizeConfig())
  assert.equal(d.length, 1)
  assert.equal(d[0].module, 'calc')
})

test('turn: num.py call suppresses calc warning', () => {
  const d = checkTurn(turnEvents({ numTool: true }), normalizeConfig())
  assert.deepEqual(d, [])
})

test('turn: code block is not flagged', () => {
  const d = checkTurn(turnEvents({ user: '看这段代码', answer: '```js\nconst x = a + b\n```' }), normalizeConfig())
  assert.deepEqual(d, [])
})

test('turn: calc disabled returns empty', () => {
  const d = checkTurn(turnEvents(), normalizeConfig({ features: { calc: { enabled: false } } }))
  assert.deepEqual(d, [])
})

test('turn: num tool detection accepts configured names', () => {
  assert.equal(hasNumToolCall(turnEvents({ numTool: true }), ['num.py']), true)
  assert.equal(hasNumToolCall(turnEvents({ numTool: false }), ['num.py']), false)
})

test('turn: command plans only for configured commands', () => {
  const cfg = normalizeConfig({ features: { layout: { command: 'python check_layout.py' }, issues: { command: '' }, repro: { command: 'python verify.py' } } })
  const plans = planTurnCommands(cfg)
  assert.deepEqual(plans.map(p => p.feature).sort(), ['layout', 'repro'])
})
