# DSH Context Compactor

一个跑在本地 Ollama 上的长会话上下文压缩管道（L0–L5）：把 DSH 会话压成分段摘要、锚点与稳定的状态 JSON，并在每轮增量合并时做确定性校验。

> 本仓库是从一个私有中文小说写作工作区中抽出的**工具代码**，不含小说正文、图片、模型权重或真实会话日志。代码按 Windows 优先设计，其他平台可参考 `src/qwen_client.py` 的 HTTP 客户端。

## 它做什么

```text
session.v4.jsonl.zstd
   │  L0 瘦身（工具流水过滤/分块）
   ▼
   │  L1 锚点（路径/编辑/CMD，kind-aware normalization）
   ▼
   │  L2 分段摘要（facts/quotes 逐字校验、薄摘要重摘）
   ▼
   │  L3 变更集合并（JSON Schema 约束解码）
   ▼
   │  L4 校验/两阶段提交（idx+expect、base_hash、fail-closed）
   ▼
   │  L5 垃圾回收（字节预算 + 归档守恒）
   ▼
02_文档/会话状态/状态.json  +  每轮 changes.json
```

核心模块（`src/_试作/`，保留原项目目录名以兼容相对路径）：

| 模块 | 作用 |
|---|---|
| `compact_context.py` | 驱动：跑 L0–L5、锁、checkpoint、后台续跑、pending 排队 |
| `qwen_client.py` | 本地 Ollama HTTP 客户端（JSON/约束解码、`done_reason`、自动拉起） |
| `slim_v2_27b.py` / `anchors_v2_27b.py` | L0/L1 瘦身与锚点提取 |
| `sum_v3_27b.py` / `thin_check.py` | L2 摘要与空壳检测 |
| `l4_v3_27b.py` / `schema_contract.py` | L3/L4 校验、契约常量与 schema 比对 |
| `gc_v2_27b.py` | L5 预算回收与归档 |
| `merge_budget.py` / `job_marker.py` / `pending_queue.py` / `service_wait.py` | 预算、任务标识、排队、服务等待 |
| `delegate.py` / `delegate_checks.py` | 本地 27B 派活（契约 lint + 验收反馈） |

## 快速开始

1. **Python 3.12+**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. **Ollama + 模型**

```powershell
# 模型目录按你的实际位置改
$env:OLLAMA_MODELS='C:\path\to\ollama-models'
$env:QWEN_MODEL='qwen3.8:27b-mtp-q4_K_M'   # 换成你本地有的模型
.\\.venv\\Scripts\\python.exe src\qwen_client.py --ensure-service
.\\.venv\\Scripts\\python.exe src\qwen_client.py --ping
```

3. **跑一轮压缩**

```powershell
.\\.venv\\Scripts\\python.exe src\_试作\compact_context.py --last-turns 1 --dry-run
# 去掉 --dry-run 才会写 02_文档/会话状态/
```

4. **DSH 插件（可选）**

`plugins/dsh_plugin_compactor/` 是 DSH Host 插件；把 `plugins/dsh_plugin_compactor/cordis.patch.yml` 里的 `projects` 改成你的工作区路径后安装。插件默认用 `pythonw.exe`，避免 GUI 宿主弹出控制台窗口。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `QWEN_URL` | `http://127.0.0.1:11434/api/chat` | Ollama chat 地址 |
| `QWEN_MODEL` | `qwen3.8:27b-mtp-q4_K_M` | 模型名 |
| `OLLAMA_MODELS` | Ollama 默认 | 模型目录；自动拉起 `ollama serve` 时继承 |
| `QWEN_NO_AUTOSTART` | 未设置 | 设为 `1` 禁用自动拉起 |

## 测试

```powershell
.\\.venv\\Scripts\\python.exe tests\run_offline.py
```

离线测试不需要 Ollama/GPU；覆盖 L4 契约、预算裁剪、排队、任务标识、GBK 控制台、摘要空壳检测等。`src/_试作/test_pipeline.py` 用合成会话跑端到端（不调模型）。

## 不包含

- 小说正文、插图、素材、模型 LoRA 权重
- 真实会话日志、会话状态、日志、备份
- 项目问题清单与审查报告（内部数据）

## 设计文档

- [上下文压缩](docs/上下文压缩.md)
- [开发约定](docs/开发约定.md)
- [委托协议](docs/委托协议.md)
- [审查规程](docs/审查规程.md)
- [DSH 环境注入上游补丁方案](docs/DSH环境注入_上游补丁方案.md)

## License

Apache License 2.0，见 [LICENSE](LICENSE)。文档与代码同许可。
