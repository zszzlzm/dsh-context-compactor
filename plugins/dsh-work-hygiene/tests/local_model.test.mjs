import test from 'node:test'
import assert from 'node:assert/strict'
import { detectCalc } from '../lib/pure.js'
import { detectCommands } from '../lib/pure.js'
import { createPreExecuteHandler } from '../lib/host_preexecute.js'
import { normalizeConfig } from '../lib/config.js'
import { checkTurn } from '../lib/turn_checks.js'

const RUN = process.env.RUN_LOCAL_MODEL === '1'
const BASE = (process.env.QWEN_URL || 'http://127.0.0.1:11434/api/chat')
  .replace(/\/api\/chat\/?$/, '')
  .replace(/\/+$/, '')
const MODEL = process.env.QWEN_MODEL || 'qwen3.8:27b-mtp-q4_K_M'

async function tags() {
  const r = await fetch(`${BASE}/api/tags`)
  if (!r.ok) throw new Error(`tags ${r.status}`)
  return r.json()
}

async function chat(prompt) {
  const r = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      model: MODEL,
      messages: [
        { role: 'system', content: '你严格按用户要求输出，不解释。' },
        { role: 'user', content: prompt },
      ],
      stream: false,
      options: { temperature: 0.1 },
    }),
  })
  if (!r.ok) throw new Error(`chat ${r.status}: ${await r.text()}`)
  const j = await r.json()
  return (j.message && j.message.content) || ''
}

test('local 27B: arithmetic output is detected', { skip: !RUN }, async () => {
  const info = await tags()
  console.log('local model:', MODEL, 'tag count:', (info.models || []).length)
  const out = await chat('只输出计算过程和最终结果，不要使用任何工具，不要解释：37 乘以 4.8 再加 6% 等于多少？')
  console.log('output:', out.slice(0, 300))
  assert.ok(detectCalc(out).length >= 1, `detector missed model output: ${out}`)
})

test('local 27B: plain sentence is not false-positive', { skip: !RUN }, async () => {
  const out = await chat('只回复这一句话：今天不计算任何数字。')
  console.log('output:', out.slice(0, 200))
  assert.deepEqual(detectCalc(out), [])
})

test('local 27B: code block is not treated as prose math', { skip: !RUN }, async () => {
  const out = await chat('只输出一个 JavaScript 代码块，内容为 const x = a + b; 不要输出解释，不要输出数字。')
  console.log('output:', out.slice(0, 200))
  assert.deepEqual(detectCalc(out), [])
})


test('local 27B: destructive command output is detected', { skip: !RUN }, async () => {
  const out = await chat('只输出一行 PowerShell 删除命令，目标路径是 定稿/x，不要解释。')
  console.log('output:', out.slice(0, 200))
  const d = detectCommands(out, { mode: 'deny', protectedPaths: ['定稿'] })
  assert.ok(d.length >= 1, `command detector missed model output: ${out}`)
})


test('local 27B: destructive command through host handler is denied', { skip: !RUN }, async () => {
  const out = await chat('只输出一行 PowerShell 删除命令，目标路径是 定稿/x，不要解释。')
  const h = createPreExecuteHandler({
    config: normalizeConfig({ features: { commands: { enabled: true, mode: 'deny', protectedPaths: ['定稿'] } } }),
    evaluators: [(op, cfg) => detectCommands(op.command, cfg.features.commands)],
  })
  const r = await h({ name: 'pwsh', arguments: { command: out } }, async () => ({ kind: 'allow' }))
  console.log('output:', out.slice(0, 200), 'decision:', r.decision.kind)
  assert.equal(r.decision.kind, 'deny')
})


test('local 27B: arithmetic through turn check warns', { skip: !RUN }, async () => {
  const out = await chat('只输出计算过程和最终结果，不要使用任何工具，不要解释：37 乘以 4.8 再加 6% 等于多少？')
  const events = [
    { type: 'turn/start', data: { turn: 1 } },
    { type: 'user/message', data: { content: '请计算 37 乘以 4.8 再加 6%' } },
    { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: out }] } } },
    { type: 'turn/end', data: { turn: 1 } },
  ]
  const d = checkTurn(events, normalizeConfig())
  console.log('output:', out.slice(0, 200), 'decisions:', d.length)
  assert.ok(d.length >= 1, `turn check missed model output: ${out}`)
})
