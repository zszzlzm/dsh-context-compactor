import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, isAbsolute } from 'node:path'

export const name = '@local/dsh-compactor-state'
export const inject = ['subprocess', 'systemPrompt']

const DEFAULTS = {
  enabled: false,
  projects: [],
  python: '.venv\\Scripts\\pythonw.exe',
  driver: 'src\_试作\\compact_context.py',
  stateFile: '02_文档\\会话状态\\状态.json',
  model: '',
  baseUrl: '',
  syncBudget: 60,
  background: true,
  injectState: true,
  skipSubagents: true,
  delayMs: 3000,
  contextOrder: 1000,
  maxStateChars: 8000,
}

const norm = p => String(p).replace(/[\\/]+$/, '').toLowerCase()
const under = (cwd, root) => {
  const c = norm(cwd), r = norm(root)
  return c === r || c.startsWith(r + '\\') || c.startsWith(r + '/')
}

function resolveSessionLog(sessionId) {
  try {
    const root = join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'sessions')
    for (const bucket of readdirSync(root)) {
      const p = join(root, bucket, String(sessionId), 'session.v4.jsonl.zstd')
      if (existsSync(p)) return p
    }
  } catch { /* ignore */ }
  return undefined
}

function renderState(path, maxChars) {
  try {
    if (!existsSync(path)) return ''
    const obj = JSON.parse(readFileSync(path, 'utf8'))
    const lines = []
    if (obj['目标'] !== undefined) {
      const goal = typeof obj['目标'] === 'string' ? obj['目标'] : JSON.stringify(obj['目标'])
      lines.push(`目标：${goal}`)
    }
    for (const key of ['待办', '已完成', '红线', '锚点']) {
      const value = obj[key]
      if (!Array.isArray(value) || value.length === 0) continue
      lines.push(`${key}：\n` + value.map(x => `- ${typeof x === 'string' ? x : JSON.stringify(x)}`).join('\n'))
    }
    if (Array.isArray(obj['决定']) && obj['决定'].length) {
      lines.push('决定：\n' + obj['决定'].map(d => `- ${d['内容'] || JSON.stringify(d)}`).join('\n'))
    }
    let text = lines.join('\n\n')
    if (!text) text = JSON.stringify(obj)
    if (text.length > maxChars) text = text.slice(0, maxChars) + '…'
    return `【上轮压缩状态（自动注入，来自 compact_context.py）】\n${text}`
  } catch {
    return ''
  }
}

export function apply(ctx, rawConfig = {}) {
  const cfg = { ...DEFAULTS, ...rawConfig }
  if (!cfg.enabled) return
  if (!Array.isArray(cfg.projects) || cfg.projects.length === 0) return
  ctx.logger?.info?.(`[compactor-state] enabled for: ${cfg.projects.join(', ')}`)
  const lastTurn = new Map()

  // 每轮提交后：起 Python 驱动，压缩刚收尾的轮（不阻塞 turn/end）
  ctx.effect(() => ctx.on('session/event', (session, event) => {
    try {
      if (!event || event.type !== 'turn/end') return
      const cwd = session?.header?.cwd
      if (!cwd) return
      if (cfg.skipSubagents !== false
          && (session.header?.origin === 'subagent' || session.header?.parentSession)) return
      const root = cfg.projects.find(p => under(cwd, p))
      if (!root) return
      const turn = event.data?.turn ?? 0
      const key = String(session.id)
      if (lastTurn.get(key) === turn) return
      lastTurn.set(key, turn)
      const logPath = resolveSessionLog(session.id)
      if (!logPath) {
        ctx.logger?.warn?.(`[compactor-state] session log not found: ${session.id}`)
        return
      }
      const python = isAbsolute(cfg.python) ? cfg.python : join(root, cfg.python)
      const driver = isAbsolute(cfg.driver) ? cfg.driver : join(root, cfg.driver)
      const argv = [python, driver, '--session', logPath, '--last-turns', '1',
        '--sync-budget', String(cfg.syncBudget),
        '--job-label', 'context-compaction', '--job-mode', 'plugin',
        '--hide-console']
      if (cfg.background) argv.push('--background')
      const env = { ...process.env, PYTHONIOENCODING: 'utf-8' }
      if (cfg.model) env.QWEN_MODEL = cfg.model
      if (cfg.baseUrl) env.QWEN_URL = cfg.baseUrl
      const timer = setTimeout(() => {
        try {
          const handle = ctx.subprocess.spawn({
            argv,
            cwd: root,
            graceMs: 5000,
            stdio: { stdin: 'ignore', stdout: { maxBytes: 20000 }, stderr: { maxBytes: 20000 } },
            env,
          })
          handle.done.then(outcome => {
            ctx.logger?.info?.(`[compactor-state] turn ${turn} compactor exit=${outcome.exitCode} signal=${outcome.signal}`)
          }).catch(err => {
            ctx.logger?.warn?.(`[compactor-state] compactor failed: ${err}`)
          })
        } catch (err) {
          ctx.logger?.warn?.(`[compactor-state] spawn threw: ${err}`)
        }
      }, Math.max(0, Number(cfg.delayMs) || 0))
      ctx.effect(() => () => clearTimeout(timer))
    } catch (err) {
      ctx.logger?.warn?.(`[compactor-state] turn handler failed: ${err}`)
    }
  }))

  // 每个 agent 创建时，往它自己的 scoped systemPrompt 注册状态 context
  ctx.effect(() => ctx.on('agent/created', ({ agent }) => {
    try {
      const cwd = agent?.session?.header?.cwd
      if (!cwd) return
      if (cfg.skipSubagents !== false
          && (agent.session.header?.origin === 'subagent' || agent.session.header?.parentSession)) return
      const root = cfg.projects.find(p => under(cwd, p))
      if (!root) return
      const target = agent?.ctx
      if (!cfg.injectState || !target) return
      const statePath = isAbsolute(cfg.stateFile) ? cfg.stateFile : join(root, cfg.stateFile)
      target.effect(() => target.systemPrompt.context({
        name: 'compactor-state',
        order: cfg.contextOrder,
        text: () => renderState(statePath, cfg.maxStateChars),
      }))
    } catch (err) {
      ctx.logger?.warn?.(`[compactor-state] agent init failed: ${err}`)
    }
  }))
}
