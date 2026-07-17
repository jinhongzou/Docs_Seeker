# DocSeeker — 文件系统自主探索智能体

## OVERVIEW
Python 项目，基于 LangGraph + ReAct 循环的文件系统探索工具。自然语言描述需求，AI 自主遍历目录、读取文件、提取内容并返回答案。

## 结构

```
DocSeeker/
├── cli.py                  # CLI 入口（argparse + asyncio）
├── conf/
│   └── config.yaml         # YAML 配置（API Key/模型/探索参数）
├── prompts/                # 提示词模板（.md 文件）
│   ├── agent_system.md     # Agent 系统提示词
│   ├── prescan.md          # 预筛选提示词
│   └── extract.txt         # 内容抽取提示词
├── SeekerDocs/             # 默认文档目录
├── sessions/               # JSONL 对话日志（--save-llm-log）
├── logs/                   # 纯文本运行日志（--save-llm-log）
├── DocSeeker.spec          # PyInstaller 打包配置
├── README.md               # 项目文档
└── src/                    # 核心 Python 包
```

## 入口点

| 文件 | 用途 |
|------|------|
| `cli.py` | 主入口，argparse 解析 + asyncio 运行 Agent |
| `src/graph.py` | `create_agent()` 构造 LangGraph 工作流 |
| `src/mcp_server.py` | MCP 服务入口（FastMCP，供 Claude Desktop 等调用） |
| `src/__init__.py` | 导出 `ContentExtractor`, `create_agent`, `run_agent` |

## 命令

```bash
# 单次查询
python cli.py "查询内容" --root SeekerDocs --verbose
# 交互模式
python cli.py --interactive
# 调试
python cli.py "查询" --debug
# 保存日志
python cli.py "查询" --save-llm-log
# MCP 服务（供 Claude Desktop 等调用）
python cli.py --mcp
```

## 配置

优先级：**CLI 参数 > conf/config.yaml > 硬编码默认值**

```bash
python cli.py "查询" -i 15  # 覆盖 max_iterations
```

## 关键约定

- **纯文本 ReAct**：Agent 输出 `ACTION:` / `ANSWER:` 格式，不依赖 LLM 原生 tool_calling
- **中文 prompt**：所有 System Prompt 都用中文（保存在 `prompts/` 目录）
- **路径容错**：`utils.resolve_path()` 自动纠正模型拼写错误的路径
- **异步并行**：文件读取、分块处理使用 `asyncio.gather` 并行化
- **双日志输出**：`--save-llm-log` 同时生成 JSONL（sessions/）和纯文本（logs/）

## 已知注意事项

- 需要 `OPENAI_API_KEY` 环境变量或 conf/config.yaml 中的 api_key
- 依赖：`langgraph`, `langchain-openai`, `markitdown`, `pyyaml`, `fastmcp`
- 无测试、无 CI、无 .env（配置在 conf/config.yaml）
- 打包用 PyInstaller（.spec 文件）
