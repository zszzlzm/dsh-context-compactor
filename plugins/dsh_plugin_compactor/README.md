# dsh-compactor-state

DSH Host 插件：每轮结束后启动本仓库的 `compact_context.py`，把状态 JSON 注入该 agent 的 scoped system prompt。

安装前修改 `cordis.patch.yml`：

- `projects`: 你的工作区绝对路径
- `python`: 建议 `pythonw.exe`（GUI 宿主不会弹控制台）
- `driver`: `src\_试作\compact_context.py`
- `model` / `baseUrl`: 你的 Ollama 模型与地址

插件本身不含模型和会话数据。
