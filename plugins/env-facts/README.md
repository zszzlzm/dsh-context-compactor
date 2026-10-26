# env-facts

在运行时上下文快照里加一段**机器可读**的环境事实块，并带 `As of` 生成时间：

```
As of 2026-10-25T12:34:56.789Z
<env>
cwd: "/path/to/your/workspace"
file_policy: danger-full-access
approval_policy: never
gui_url: http://127.0.0.1:3442
session: 019...
</env>
```

## 为什么这样实现 as-of

快照文本参与 DSH 的去重：环境不变时不重复发消息（实测 186 步只有 1 条）。如果每步写新时间戳，
去重会失效、每步都重发。本插件按会话缓存：**facts 变了才重算文本**，`As of` 表示"这些事实最后一次变化的时刻"
（首次为捕获时刻），因此环境稳定时文本逐字不变，去重保持有效。

## 事实来源

| 字段 | 来源 |
|---|---|
| `cwd` | `workingDirectory.get(session)` |
| `file_policy` | `sandboxPolicy.resolve({ session }).mode` |
| `approval_policy` | `approval.overrideOf(session)`（无覆盖时省略） |
| `gui_url` | `webApp.getAdvertisedUrl()` |
| `session` | `session.id` |

缺失的服务或事实直接省略，不会让 assembly 失败。

## 安装

由 `plugin_manager` 以本地 bundle 安装（`dsh.bundle.patch` 插入 `env-facts` 一行）。

## 去重与 compaction（P-050）

快照文本在环境稳定时逐字不变（`As of` 只在 facts 变化时更新），插件侧缓存是稳定的。
DSH 在 **compaction 替换旧 surface 区间**时会把 retained 快照清空，之后会重发一次当前快照——
这是预期补发，不是缓存失效。账本 `verify_env_injection_numbers.py` 按 replacement 事件分类，
只对「未解释的相邻同文本快照」硬断言 0。

## 升级回归（P-048）

DSH 升级后跑：

```
python src\_试作\verify_env_upgrade_regression.py
```

脚本会读 DSH 版本、扫描上游是否已出现 env-facts 标记、检查本插件是否安装，并重跑
`verify_env_injection_numbers.py`；与 `06_日志/env_facts_baseline.json` 对比。

- 版本变化：提示复核，确认后用 `--update-baseline` 更新基线
- 上游合入：退出码 3，提示卸载 `@local/env-facts`、保留数字账回归
