# DSH 环境状态注入：上游最小补丁方案

- 依据审查：2026-10-25_DSH运行时环境状态注入.md（总体 B+，对应提案一/二）
- 本机构建指纹：`app.asar` 大小 182,434,783 字节，写入时间 2026-10-23 16:00:56
- 文件指纹（SHA256 前 16 位，用于对齐上游版本）：

| 文件 | 字节 | SHA256-16 |
|---|---|---|
| `dsh-system-prompt/lib/index.js` | 19817 | 7be9f7aafbf345d0 |
| `dsh-agent-loop/lib/index.js` | 74599 | cf57e3ecd487ac5c |
| `dsh-working-directory/lib/index.js` | 6916 | af5bb7f33c52efe0 |
| `dsh-sandbox-policy/lib/index.js` | 7553 | 772ca58f0f786d6c |
| `dsh-user-approval/lib/index.js` | 8478 | 6a5ad1ba3c3dc3ab |
| `dsh-harness-dev-context/lib/index.js` | 7357 | c384040ee0aeb3a0 |
| `dsh-experimental-tool-agent-team/lib/index.js` | 18903 | cac22fe5f83a1790 |
| `dsh-subagent/lib/index.js` | 141871 | 9a9572e70fffdb10 |

> 本方案**不改本机安装包**：只给上游的改动清单、片段与验收。
> **本机先行实现**：同样的目标已做成 Host 插件 `@local/env-facts`（`src/dsh_plugins/env-facts`），
> 走 `systemPrompt.context` 注入，不影响安装包；它在 facts 变化时才重算文本，从而保住去重。
> 以下是给上游的最小补丁——插件只能覆盖"我们渲染的事实"，整份快照的 as-of 与其它 provider 的 facts
> 仍要上游改。

## 一、目标

1. 运行时上下文快照带**生成时刻**：`As of <ISO 8601>`。
2. 同一快照里附**机器可读事实块**（`key: value`），不破坏现有的人读散文。

## 二、关键设计决定（为什么不是"每步盖新时间戳"）

快照文本参与去重：`RuntimeContextProjection.project` 在文本相等时不发消息。若每步写一个新时间戳，
**去重会失效**——本机实测 186 步只产生 1 条快照，按每步盖戳会变成 186 条，上下文与缓存成本倒退。

因此补丁采用：

- **去重仍按 body**（不含时间戳）
- **只在"要发新快照"时盖一次 `As of`**，语义是"这条快照的生成时刻"
- 当前钟继续由 time-context 提供（它的节流问题是提案三，不在本补丁内）

## 三、变更清单

| # | 文件 | 位置 | 改动 | 量级 |
|---|---|---|---|---|
| 1 | `dsh-system-prompt/lib/index.js` | `renderContextSections` / `joinContextSections` | context 注册增加可选 `facts`；`join` 拼 `<env>` 块；可选 `asOf` 形参 | 约 20 行 |
| 2 | `dsh-agent-loop/lib/index.js` | `RuntimeContextProjection` | 记录/比较 **body**；发送时插入 `As of`；恢复时剥离 | 约 15 行 |
| 3 | `dsh-working-directory/lib/index.js` | context 注册 | `facts: (c) => ({ cwd })` | 1 行 |
| 4 | `dsh-sandbox-policy/lib/index.js` | context 注册 | `facts: (c) => ({ file_policy })` | 1 行 |
| 5 | `dsh-user-approval/lib/index.js` | context 注册 | `facts: (c) => ({ approval_policy })` | 1 行 |
| 6 | `dsh-experimental-tool-agent-team/lib/index.js` | `team:identity` | `facts: { team_role, lead_name }` | 1 行 |
| 7 | `dsh-harness-dev-context/lib/index.js` | `harness:source` / `web:surface` | `facts: { checkout, gui_url }` | 2 行 |
| 8 | `dsh-subagent/lib/index.js` | `subagent:delegation`（可选） | `facts: { delegation }` | 1 行 |

未提供 `facts` 的 context 行为完全不变（向后兼容）。

## 四、核心改动片段

### 4.1 `dsh-system-prompt/lib/index.js`

现状（`renderContextSections` 丢弃了除 name/text 之外的元数据）：

```js
return assembly.contexts.map((context) => ({
    name: context.name,
    text: context.interpolate === false ? context.text : interpolate(context, assembly.variables, "context")
})).filter((section) => section.text.length > 0);
```

改为透传已解析的 `facts`（与 `text` 同形：值或函数，由 `resolveContext` 统一求值）：

```js
function resolveContext(entry, context) {
    return {
        name: entry.name,
        text: typeof entry.text === "function" ? entry.text(context) : entry.text,
        ...entry.facts === void 0 ? {} : {
            facts: typeof entry.facts === "function" ? entry.facts(context) : entry.facts
        },
        ...entry.interpolate !== void 0 ? { interpolate: entry.interpolate } : {}
    };
}

function renderContextSections(assembly) {
    return assembly.contexts.map((context) => ({
        name: context.name,
        text: context.interpolate === false ? context.text : interpolate(context, assembly.variables, "context"),
        ...context.facts === void 0 ? {} : { facts: context.facts }
    })).filter((section) => section.text.length > 0);
}
```

`joinContextSections` 增加可选 `asOf`，并把各 section 的 facts 合成 `<env>` 块：

```js
function joinContextSections(sections, asOf) {
    const body = sections.map((section) => section.text).join("\n\n");
    if (body.length === 0) return "";
    const stamp = asOf === void 0 ? "" : `\nAs of ${asOf}`;
    const facts = sections.flatMap((section) => section.facts === void 0 ? [] : Object.entries(section.facts));
    const env = facts.length === 0 ? "" : `\n\n<env>\n${facts.map(([key, value]) => `${key}: ${value}`).join("\n")}\n</env>`;
    return `Current runtime context. This snapshot supersedes earlier runtime-context snapshots.${stamp}\n\n${body}${env}`;
}
```

`asOf` 缺省时输出与现状逐字相同（现有单测可原样断言）。

### 4.2 `dsh-agent-loop/lib/index.js`

`RuntimeContextProjection` 现在只记 `text`；改为同时记 `body`，去重比 body，发送时盖戳：

```js
// 恢复时（构造函数里两处赋值）
this.retained = { seq: event.seq, text, body: stripAsOf(text) };
this.retained = { seq: event.seq, text: textOf(event.data), body: stripAsOf(textOf(event.data)) };

// 发送时
project(current, sections) {
    if (this.retained === void 0 && current.length === 0) return;
    const body = current.length === 0 ? CLEARED : current;
    if (this.retained?.body === body) return;
    const text = current.length === 0 ? body : `Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\nAs of ${new Date().toISOString()}\n\n${current}`;
    return createUserMessage({ content: [{ type: "text", text }], source: ... });
}
```

辅助函数（同文件内）：

```js
const AS_OF = /^As of \S+$/m;
function stripAsOf(text) {
    return text.replace(AS_OF, "").replace("\n\n\n", "\n\n");
}
```

`preStep` 与 `step` 里的两个调用点不用改（仍是 `project(joinContextSections(sections), sections)`）。

### 4.3 provider 示例（`dsh-working-directory`）

```js
ctx.systemPrompt.context({
    name: "working-directory:current",
    order: ctx.systemPrompt.getContextOrder("WORKING_DIRECTORY"),
    required: true,
    interpolate: false,
    text: (context) => context.agent === void 0 ? "" : this.contextText(context.agent.session),
    facts: (context) => context.agent === void 0 ? {} : { cwd: this.get(context.agent.session) }
});
```

其余 provider 同形（`file_policy` / `approval_policy` / `team_role`+`lead_name` / `checkout` / `gui_url`）。

## 五、测试与验收

| 层级 | 断言 | 通过标准 |
|---|---|---|
| 单元（上游现有测试） | `joinContextSections(sections)` 不传 asOf 输出逐字不变 | 原有断言零改动通过 |
| 单元 | 传 asOf 时快照含一行 `As of <ISO>` | 新断言 1 条 |
| 单元 | 两个 provider 带 facts 时快照含 `<env>` 且 ≥5 行 `key: value` | 新断言 1 条 |
| 集成（本机黑盒） | 同一环境连续步：快照仍为 1 条（去重不破） | 数字账的"每步 ≤1、不同文本数==快照数"保持 |
| 集成 | 快照含 as-of 与 ≥5 行 key: value | 数字账把"无时间戳"断言翻成"有"，新增 1 条 |
| 回归 | 快照增量 < 300 字 | 记录实测值；超了先查 facts 是否重复承载 |

本机核对命令：

```powershell
.\.venv\Scripts\python.exe src\_试作\verify_env_injection_numbers.py
```

## 六、风险与回滚

- **时间戳导致去重失效**：本方案最需要盯的点，已用"去重比 body、发送才盖戳"规避；集成断言必须含"连续步只有 1 条"。
- **facts 与散文重复**：facts 只放短值（路径/枚举），不放长解释；长文本继续留在 text。
- **缓存**：快照是 user message，不进 system prompt 前缀；`As of` 只在发新快照时出现，不会每步失效缓存。
- **回滚**：移除 `asOf`/`stripAsOf` 即回现状；provider 的 `facts` 删掉即可，两者互不依赖。

## 七、上游提交建议

- 可直接 `git apply` 的补丁包：[2026-10-25_DSH环境注入_上游补丁包](README.md)
  （基线 tag `dsh-v0.2.1-rc.3`，两个补丁 + PR 描述；每个补丁都做过"回放还原修改后文件"的逐字校验）
- 两个 commit：
  1. `feat(system-prompt): export RUNTIME_CONTEXT_HEADER`
  2. `feat(agent-loop): stamp runtime-context snapshots with As of`
- PR 说明直接引用本机证据：快照 839 字、186 步 1 条（证明"每步盖戳会破坏去重"）、现状无时间戳。
- 附上本方案"单元 + 集成"验收表，作为评审检查单。
- 上游 Issues 是关闭的：走 PR（先读 `CONTRIBUTING.md` / `CONTRIBUTING.zh.md`）。
