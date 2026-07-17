# src/ — Core Python Package

## 模块一览

| 文件 | 行数 | 职责 |
|------|------|------|
| `graph.py` | 670 | LangGraph 工作流：prescan → agent ↔ tools 循环 |
| `tools.py` | 401 | 6 个文件系统工具（闭包注入共享实例） |
| `content_extractor.py` | 420 | MarkItDown + LLM 结构化抽取（支持大文件并行分块） |
| `utils.py` | 258 | 路径纠正、ACTION 解析、对话截断（纯函数，无 LLM 依赖） |
| `config.py` | 207 | AgentConfig + YAML/CLI/env 三级配置加载 |
| `prompts.py` | 18 | 从 `prompts/` 目录加载提示词（prescan/extract/agent_system） |
| `state.py` | 39 | ExplorerState TypedDict（conversation 字符串存储） |
| `schemas.py` | 16 | Pydantic 模型：ExtractedSegment, ExtractResult |

## WHERE TO LOOK

- **改工作流逻辑** → `graph.py`（`create_agent`、`should_continue` 条件边）
- **增/改工具** → `tools.py`（`create_tools` 闭包函数）
- **改配置加载** → `config.py`（`build_config`）
- **改提示词** → `prompts/*.md`（通过 `prompts.py` 加载）
- **修路径解析** → `utils.py`（`resolve_path`、`parse_action`）

## 约定

- 工具函数以 `tool_name` 字符串键存在 `dict` 中，非 class
- ContentExtractor 实例在 `create_tools()` 内创建，通过闭包注入各工具
- `conversation` 是纯文本字符串，格式：`助手:\n...\n\n---OBSERVATION---\n...`
- 异步工具（`extract_content`、`batch_extract`）使用 `asyncio.gather` 并行处理

## ANTI-PATTERNS

- **不要改 conversation 格式**（graph.py/tools_node 依赖 `助手:\n` 和 `---OBSERVATION---` 分割）
- **不要移除路径容错**（utils.resolve_path 是模型拼写错误的最后防线）
- **不要突然切换中文 prompt 为英文**（全部 prompt 都是中文）
- **不要把 prompts.py 改回内联字符串**（提示词已外置到 `prompts/` 目录）
