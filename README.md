# DocSeeker

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-orange.svg)](https://github.com/langchain-ai/langgraph)

## 告别翻文件，用自然语言找到答案

> "找关于贷款风险分析的内容" → AI 自动遍历目录、读取文件、精准提取相关段落

DocSeeker 是一个**文件系统自主探索智能体**。你用自然语言提问，AI 自动规划路径、读取文件、提取内容，返回精准答案。

```
$ python cli.py "哪些文档提到了数据隐私保护？"

🔍 正在探索文件系统...
📁 扫描到 47 个文件
🎯 筛选出 8 个候选文件
📖 正在读取相关文档...

找到以下相关文档：
1. company_policy.md - 第 23-45 行
   "根据《个人信息保护法》第十七条，公司必须..."
2. security_audit_2024.pdf - 第 12-18 行
   "数据隐私保护措施已通过 ISO 27001 认证..."
3. employee_handbook.docx - 第 89-92 行
   "员工有责任保护客户数据隐私..."
```

## 为什么选择 DocSeeker？

| 传统方式 | DocSeeker |
|----------|-----------|
| 手动翻阅 50 个文件 | 一句话找到答案 |
| 记住文件名和位置 | 自然语言提问 |
| 逐个打开文件搜索 | AI 自动遍历所有文件 |
| 复制粘贴到 ChatGPT | 直接返回精准段落 |

## 快速开始

```bash
# 安装
git clone https://github.com/yourname/DocSeeker.git
cd DocSeeker
pip install langgraph langchain-openai markitdown pyyaml fastmcp

# 配置
cp conf/config.yaml.example conf/config.yaml
# 编辑 conf/config.yaml，填入你的 API Key

# 运行
python cli.py "找关于贷款风险分析的内容"
python cli.py --interactive  # 交互模式
```

## 核心亮点

### 6 种文件工具

| 工具 | 用途 |
|------|------|
| `list_directory` | 列出目录内容（含文件大小） |
| `read_file` | 读取文件内容（多格式） |
| `search_files` | 按文件名模式递归搜索 |
| `grep_content` | 搜索文件内容（grep） |
| `extract_content` | 从单个文件精准提取相关片段 |
| `batch_extract` | 批量提取目录下所有文件 |

### 大文件并行分块

超过 500 行的文件自动分块，`asyncio.gather` 并行处理，大幅加速大文件提取。

### 多格式支持

docx / pdf / xlsx / pptx / txt / md / csv / json / html —— 通过 MarkItDown 统一转换，无需手动选择解析器。

### 路径容错

自动纠正模型拼写错误的路径（如 `SeekarDocs` → `SeekerDocs`），减少探索失败。

### 三级配置

```
CLI 参数 > conf/config.yaml > 环境变量 > 默认值
```

### 交互模式

多轮对话，上下文自动保持，适合连续探索：

```bash
python cli.py --interactive
```

## 工作原理

```
用户输入（自然语言）
        ↓
   ┌──────────┐
   │ Prescan  │  递归扫描文件树 → LLM 筛选候选文件
   └────┬─────┘
        ↓
    ┌─────────┐     ACTION     ┌─────────┐
    │  Agent  │ ─────────────→ │  Tools  │
    │  (LLM) │ ←───────────── │ (执行)  │
    └────┬────┘   OBSERVATION  └─────────┘
         │
         ↓ ANSWER
      最终答案
```

1. **预扫描** — 递归扫描根目录生成文件树，LLM 根据查询筛选最相关的候选文件
2. **ReAct 循环** — Agent 带着候选列表开始探索，优先读取候选文件，以最少工具调用完成任务

## 详细配置

支持任何 OpenAI 兼容 API（OpenAI、SiliconFlow、Ollama、vLLM 等）。

```bash
# 环境变量配置
export OPENAI_API_KEY=sk-your-key
export OPENAI_BASE_URL=https://api.siliconflow.cn/v1
export OPENAI_MODEL=Qwen/Qwen2.5-7B-Instruct

# 单次查询
python cli.py "SeekerDocs 目录下有什么文件"
python cli.py "找关于贷款风险的分析" --verbose

# 保存对话日志
python cli.py "查询内容" --save-llm-log
```

## 命令行参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `query` | — | 自然语言查询（位置参数） | 无（进入交互模式） |
| `--root` | `-r` | 起始目录 | `SeekerDocs` |
| `--api-key` | `-k` | API Key | 从 conf/config.yaml 读取 |
| `--model` | `-m` | 模型名称 | `Qwen/Qwen2.5-7B-Instruct` |
| `--base-url` | `-u` | API 地址 | `https://api.siliconflow.cn/v1` |
| `--max-iterations` | `-i` | 最大探索轮数 | `10` |
| `--maxTokens` | — | LLM 上下文 token 上限 | `32768` |
| `--max-file-lines` | — | read_file 单文件最大读取行数 | `500` |
| `--max-chunk-lines` | — | extract_content 大文件分块行数 | `500` |
| `--max-snippet-length` | — | extract_content 每段 snippet 最大字符数 | `600` |
| `--max-context-rounds` | — | 保留最近 N 轮对话 | `6` |
| `--max-file-size` | — | 文件读取大小上限（MB） | `50` |
| `--max-tree-depth` | — | 预扫描文件树最大递归深度 | `4` |
| `--max-tree-files` | — | 预扫描文件树最大文件数 | `200` |
| `--max-steps` | — | Agent 典型完成步骤数 | `4` |
| `--verbose` | — | 显示详细探索过程 | 关闭 |
| `--debug` | — | 打印 LLM 完整对话内容 | 关闭 |
| `--no-prescan` | — | 跳过预扫描 | 关闭 |
| `--save-llm-log` | — | 保存对话日志到 sessions/ 和 logs/ | 关闭 |
| `--mcp` | — | 启动 MCP 服务（供 Claude Desktop 等 Agent 调用） | 关闭 |
| `--mcp-transport` | — | MCP 传输方式（stdio / sse） | `stdio` |
| `--interactive` | — | 交互模式 | 关闭 |

## 项目结构

```
DocSeeker/
├── cli.py                  # CLI 入口
├── conf/
│   ├── config.yaml         # 你的配置（git 忽略）
│   └── config.yaml.example # 配置模板
├── prompts/                # 提示词模板（.md 文件）
│   ├── agent_system.md     # Agent 系统提示词
│   ├── prescan.md          # 预筛选提示词
│   └── extract.md          # 内容抽取提示词
├── SeekerDocs/             # 默认文档目录
├── sessions/               # JSONL 对话日志（git 忽略）
├── logs/                   # 纯文本运行日志（git 忽略）
├── DocSeeker.spec          # PyInstaller 打包配置
└── src/
    ├── __init__.py
    ├── config.py           # 三级配置加载（YAML/环境变量/CLI）
    ├── content_extractor.py # MarkItDown + LLM 提取（并行分块）
    ├── graph.py            # LangGraph 工作流（ReAct + 预扫描）
    ├── tools.py            # 6 个文件系统工具
    ├── mcp_server.py       # MCP 服务（FastMCP，供外部 Agent 调用）
    ├── utils.py            # 路径纠正、ACTION 解析、对话截断、Token 估算
    ├── prompts.py          # 从 prompts/ 加载提示词
    ├── schemas.py          # Pydantic 数据模型
    └── state.py            # ExplorerState 状态定义
```

## 日志系统

`--save-llm-log` 同时生成两种日志：

| 目录 | 格式 | 文件名 | 内容 |
|------|------|--------|------|
| `sessions/` | JSONL | `llm_YYYYMMDD_HHMMSS.jsonl` | 结构化对话（user/assistant/tool） |
| `logs/` | 纯文本 | `run_YYYYMMDD_HHMMSS.txt` | 人类可读的完整对话过程 |
| `sessions/` | JSONL | `history.jsonl` | 查询历史记录 |

## 横向对比：DocSeeker vs Karpathy LLM 项目 vs 传统 RAG

DocSeeker、Karpathy 的 LLM 项目、传统 RAG 三者处于 AI 技术栈的不同层次，解决不同问题，**不是竞品关系，而是互补关系**。

### 三方定位对比

| 维度 | DocSeeker | Karpathy LLM 项目 | 传统 RAG |
|------|-----------|-------------------|----------|
| **层次** | 应用层（Agent 工具） | 基础层（LLM 原理） | 应用层（检索系统） |
| **目标** | 文档检索智能体 | 教学/研究 LLM 原理 | 语义检索 + 生成 |
| **技术栈** | LangGraph + ReAct + MarkItDown | PyTorch + C/CUDA | 向量数据库 + Embedding |
| **输入** | 自然语言查询 | 训练数据 / 模型权重 | 查询 + 文档库 |
| **输出** | 文档内容答案 | 训练好的模型 | 检索结果 + 生成答案 |
| **部署** | pip install 即用 | 需要 GPU + 训练 | 需要向量数据库 |
| **学习曲线** | 低（会用命令行） | 高（需要 ML 基础） | 中（需要了解向量检索） |

### 技术层次关系

```
┌─────────────────────────────────────────────────────────────────┐
│  应用层                                                         │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │  DocSeeker      │    │  传统 RAG       │                     │
│  │  (Agent 探索)   │    │  (向量检索)     │                     │
│  └────────┬────────┘    └────────┬────────┘                     │
│           │                      │                              │
│           └──────────┬───────────┘                              │
│                      ↓ 调用                                     │
│  框架层：LangGraph / ReAct（Agent 编排）                         │
│                      ↓ 调用                                     │
│  模型层：GPT-4o / Qwen / Llama（LLM 推理）                      │
│                      ↓ 训练于                                   │
│  基础层：nanoGPT / llm.c（LLM 训练/推理）                       │
└─────────────────────────────────────────────────────────────────┘
```

### Karpathy 项目速览

| 项目 | Stars | 定位 | 与 DocSeeker 的关系 |
|------|-------|------|---------------------|
| **nanoGPT** | 61k+ | GPT 训练/微调 | DocSeeker 可调用其训练的模型 |
| **llm.c** | 30k+ | C 语言训练 LLM | 底层训练效率优化 |
| **llama2.c** | 19k+ | C 语言推理 Llama | 底层推理部署 |
| **micrograd** | 16k+ | 自动微分引擎 | 深度学习基础组件 |
| **minGPT** | 24k+ | GPT 教学实现 | 已被 nanoGPT 取代 |

### 核心差异解析

#### DocSeeker vs Karpathy：用 LLM vs 造 LLM

| 维度 | DocSeeker | Karpathy 项目 |
|------|-----------|---------------|
| **角色** | LLM 的"消费者" | LLM 的"生产者" |
| **问题** | 如何用自然语言找到文档信息？ | 如何理解/训练/部署 LLM？ |
| **技术** | Agent 编排 + 文件工具 | 模型训练 + 推理优化 |
| **用户** | 普通用户、企业 | AI 研究者、学习者 |

#### DocSeeker vs 传统 RAG：Agent 探索 vs 向量检索

| 维度 | DocSeeker | 传统 RAG |
|------|-----------|----------|
| **预处理** | 无需预处理，直接读文件 | 分块 → 向量化 → 建索引 |
| **流程** | LLM 自主决定读哪个文件 → 实时读取 → 推理 | 查询向量化 → 向量检索 → 取 top-K 块 → LLM 生成 |
| **存储** | 文件系统 | 向量数据库（FAISS/Milvus/Pinecone） |
| **结构感知** | ✅ 理解目录层级、文件命名 | ❌ 只看文本块，忽略文件结构 |
| **交互探索** | ✅ 多轮对话，逐步深入 | ❌ 单次检索，无法追问 |
| **语义匹配** | ❌ 依赖关键词 grep | ✅ 向量相似度匹配 |
| **大规模效率** | ❌ 10 万+ 文件时慢 | ✅ 毫秒级响应 |

### DocSeeker 的优劣势

#### 优势

- **零预处理** — 直接指向文档目录即可使用，无需分块、向量化、建索引
- **结构感知** — Agent 能理解目录层级、文件命名、组织关系
- **交互探索** — 支持多轮对话，逐步深入（先看目录 → 再看文件 → 再找相关文件）
- **无向量数据库依赖** — 部署简单，无运维成本
- **实时一致** — 直接读源文件，不存在索引过期问题
- **工具可扩展** — grep、extract 等多种检索手段，不局限于向量相似度

#### 劣势

- **LLM 调用成本高** — 每次工具调用都消耗 token，一个复杂查询可能调用 5-10 次 LLM
- **检索速度慢** — Agent 逐文件探索 vs 向量检索毫秒级返回
- **无语义匹配** — 依赖关键词 grep，无法像向量检索那样做语义相似度匹配
- **大规模文档效率低** — 10 万+ 文件时，Agent 探索效率远不如预建索引
- **上下文窗口限制** — 单文件过大需要分块，跨文件综合分析受限于 maxTokens
- **准确性依赖 Agent 推理** — Agent 可能走错路径、遗漏相关文件

### 什么时候选哪个

| 场景 | 推荐 | 原因 |
|------|------|------|
| 文件 < 1000，需要结构化浏览 | DocSeeker | Agent 能理解目录结构，交互探索 |
| 文档目录组织清晰，需要理解上下文 | DocSeeker | 结构感知 + 多轮对话 |
| 快速原型，不想建索引 | DocSeeker | 零配置，pip install 即用 |
| 文件 > 10 万，需要语义检索 | 传统 RAG | 向量检索毫秒级响应 |
| 高频查询，需要毫秒级响应 | 传统 RAG | 预建索引，查询速度快 |
| 需要精确的法规条文定位 | 传统 RAG | 语义匹配更精准 |
| 学习 LLM 原理 | Karpathy 项目 | 从零实现，教学目的 |
| 两者结合：DocSeeker 做探索入口，RAG 做精准检索 | 混合方案 | 取长补短 |

### 互补场景示例

```
场景 1：企业文档检索
用户需求：快速查找公司政策文档
推荐方案：DocSeeker（文件少，结构清晰）

场景 2：大规模知识库
用户需求：从 100 万篇论文中检索相关研究
推荐方案：传统 RAG（需要语义匹配 + 高效检索）

场景 3：本地化部署
用户需求：用本地 Llama 模型检索公司文档（隐私安全）
推荐方案：llama2.c（部署模型） + DocSeeker（文档检索 Agent）

场景 4：学习 LLM
用户需求：理解 GPT 是如何训练的
推荐方案：nanoGPT（从零实现训练循环）
```

## MCP 服务

DocSeeker 可作为 MCP (Model Context Protocol) 服务器运行，供 Claude Desktop、Cursor、Windsurf、OpenCode 等 AI Agent 直接调用文件系统工具。

### 启动服务

```bash
python cli.py --mcp
# 或指定 SSE 传输
python cli.py --mcp --mcp-transport sse
```

### 客户端配置

#### Claude Desktop

编辑 `%APPDATA%/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "DocSeeker": {
      "command": "python",
      "args": ["C:\\path\\to\\DocSeeker\\cli.py", "--mcp"],
      "env": {
        "OPENAI_API_KEY": "your-api-key"
      }
    }
  }
}
```

#### OpenCode / Cursor / Windsurf

编辑 `~/.config/opencode/opencode.json`，在 `mcp` 字段中添加：

```json
{
  "mcpServers": {
    "docseeker": {
      "command": "python",
      "args": ["C:\\Users\\Lenovo\\Desktop\\DocSeeker\\cli.py", "--mcp"]
    }
  }
}
```

重启后，在提示词中加 `use docseeker` 即可调用文件系统工具。

#### 隐私安全配置（推荐）

不想在配置文件中暴露 API Key？可以删除 `env` 字段，DocSeeker 会自动从 `conf/config.yaml` 读取：

```json
{
  "mcpServers": {
    "docseeker": {
      "command": "python",
      "args": ["C:\\Users\\Lenovo\\Desktop\\DocSeeker\\cli.py", "--mcp"]
    }
  }
}
```

或者使用环境变量引用：

```json
{
  "mcpServers": {
    "docseeker": {
      "command": "python",
      "args": ["C:\\Users\\Lenovo\\Desktop\\DocSeeker\\cli.py", "--mcp"],
      "env": {
        "OPENAI_API_KEY": "${DOCSEEKER_API_KEY}"
      }
    }
  }
}
```

```powershell
$env:DOCSEEKER_API_KEY = "你的API Key"
```

### 暴露的工具

| 工具 | 说明 |
|------|------|
| `list_directory` | 列出目录内容 |
| `read_file` | 读取文件内容 |
| `search_files` | 按模式搜索文件 |
| `grep_content` | 正则表达式搜索文件内容 |
| `extract_content` | 智能提取文件相关内容（大文件自动分块） |
| `batch_extract` | 批量提取多个文件 |

## 注意事项

- **模型质量** — 建议使用 7B 以上参数量的模型（如 Qwen2.5-14B/72B-Instruct、GPT-4o-mini），小模型可能无法稳定生成正确的工具调用格式
- **预扫描** — 额外调用一次 LLM 筛选候选文件，目录文件少时自动跳过；不需要时用 `--no-prescan` 关闭
- **路径容错** — 自动纠正模型拼写错误的路径，但严重偏差仍可能导致失败
- **Token 限制** — `maxTokens` 参数控制 LLM 上下文上限，超出时自动截断过长的对话历史/文件内容
- **Windows** — CLI 已处理 Windows 控制台 UTF-8 编码问题

## FAQ

### Q: DocSeeker 和 ChatGPT 的文件上传功能有什么区别？

**A:** ChatGPT 的文件上传是单次对话，文件内容会进入上下文窗口，受 token 限制。DocSeeker 是 Agent 模式，可以自主决定读取哪些文件、读取多少内容，支持多轮对话逐步深入，适合处理大量文档。

### Q: 支持哪些 LLM 模型？

**A:** 支持任何 OpenAI 兼容 API，包括：
- OpenAI（GPT-4o、GPT-4o-mini）
- SiliconFlow（Qwen2.5 系列）
- 本地部署（Ollama、vLLM、LM Studio）

### Q: 文件数量有上限吗？

**A:** 预扫描阶段默认最多扫描 200 个文件（可通过 `--max-tree-files` 调整）。超过此数量时，LLM 会筛选最相关的候选文件优先探索。实际使用中，1000 以内的文件效果最佳。

### Q: 如何降低 LLM 调用成本？

**A:** 
1. 使用更便宜的模型（如 Qwen2.5-7B-Instruct）
2. 减少 `--max-iterations`（默认 10）
3. 使用 `--no-prescan` 跳过预扫描（文件少时）
4. 本地部署模型（Ollama）完全免费

### Q: 能处理加密或受密码保护的文件吗？

**A:** 目前不支持。DocSeeker 依赖 MarkItDown 进行文件解析，加密文件无法读取。

### Q: 如何扩展支持新的文件格式？

**A:** DocSeeker 通过 MarkItDown 支持文件格式。如果 MarkItDown 支持某格式，DocSeeker 自动支持。对于 MarkItDown 不支持的格式，需要在 `src/content_extractor.py` 中添加自定义解析器。

## License

MIT

## 贡献

欢迎提交 Issue 和 Pull Request！请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)（如有）。
