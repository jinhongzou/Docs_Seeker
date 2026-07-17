---
name: docseeker
description: 通过 DocSeeker MCP 探索本地文件系统。列出目录、读取文件、按文件名搜索、搜索文件内容、用 LLM 智能提取文档内容。
trigger: 当用户需要探索文件、搜索文档、读取文件内容、在本地文件中查找信息、从 PDF/Word/Excel 中提取内容、或浏览目录结构时使用。触发词包括 "docseeker"、"搜索文件"、"查找文档"、"读取文件"、"grep"、"提取"、"列出文件"、"浏览目录"、"探索文件"。
---

# DocSeeker — 文件系统探索工具

DocSeeker 是一个 MCP 服务器，提供 6 个工具，帮你在本地文件系统中查找信息。你只需要用自然语言描述需求，工具会自动完成搜索和提取。

**路径规则：** 所有路径都相对于根目录（默认 `SeekerDocs/`）。比如根目录下有 `合同.docx`，路径就是 `"合同.docx"`；子目录 `docs/` 下有 `报告.pdf`，路径就是 `"docs/报告.pdf"`。

## 该用哪个工具？速查表

| 你想做什么 | 用哪个工具 |
|-----------|-----------|
| 看看目录里有什么文件 | `list_directory` |
| 读取某个文件的内容 | `read_file` |
| 找某个名字的文件（如所有 PDF） | `search_files` |
| 找包含某个关键词的文件 | `grep_content` |
| 从大文件中提取特定主题的内容 | `extract_content` |
| 从多个文件中批量提取特定主题 | `batch_extract` |

---

## 工具详情

### 1. list_directory — 看看目录里有什么

```
list_directory(path: str = ".") -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 否 | 目录路径。`"."` 是根目录，`"子目录名"` 是子目录。不填则列根目录。 |

```
list_directory(path=".")         # 列出根目录
list_directory(path="QADocs")    # 列出 QADocs 子目录
```

### 2. read_file — 读取文件内容

```
read_file(path: str, max_lines: int = 500) -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 文件路径，如 `"报告.docx"`、`"子目录/数据.csv"` |
| `max_lines` | 否 | 最多读几行，默认 500。超过 500 行的大文件建议用 `extract_content`。 |

支持格式：docx、pdf、xlsx、pptx、txt、md、csv、json、html

```
read_file(path="合同模板.docx")           # 读整个文件
read_file(path="data.csv", max_lines=100) # 只读前 100 行
```

### 3. search_files — 按文件名找文件

```
search_files(pattern: str, root_path: str = "") -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `pattern` | 是 | 文件名匹配规则，如 `"*.pdf"`（所有 PDF）、`"*.docx"`（所有 Word）、`"report*"`（以 report 开头的） |
| `root_path` | 否 | 从哪个目录开始搜，不填则从根目录搜。 |

```
search_files(pattern="*.pdf")                # 找所有 PDF
search_files(pattern="*.md", root_path="docs") # 在 docs 目录找所有 Markdown
```

### 4. grep_content — 搜索文件内容

```
grep_content(keyword: str, root_path: str = "", file_pattern: str = "*") -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `keyword` | 是 | 搜索关键词，如 `"贷款"`。也支持正则表达式，如 `"risk.*analysis"` |
| `root_path` | 否 | 从哪个目录开始搜，不填则从根目录搜。 |
| `file_pattern` | 否 | 只搜哪类文件，如 `"*.md"`。不填则搜所有文件。 |

```
grep_content(keyword="AI")                     # 找所有包含 AI 的文件
grep_content(keyword="合同", file_pattern="*.md") # 只在 .md 文件中搜"合同"
```

### 5. extract_content — 用 AI 从单个文件提取内容

```
extract_content(file_path: str, topic: str) -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `file_path` | 是 | 文件路径，如 `"季度报告.docx"` |
| `topic` | 是 | 你想找什么。可以是一个主题，如 `"贷款风险分析"`；也可以是多个关键词，用逗号分隔，如 `"营业收入, 净利润, 毛利率"` |

> **注意：** 参数名是 `file_path` 和 `topic`，不是 `path` 和 `query`。

**什么时候用：** 文件太大不想全读，只想找特定内容时用。AI 会自动分析文件，只返回和你主题相关的段落。

```
extract_content(file_path="季度报告.docx", topic="贷款风险分析")
extract_content(file_path="财务报表.xlsx", topic="营业收入, 净利润, 毛利率")
```

### 6. batch_extract — 用 AI 批量从多个文件提取内容

```
batch_extract(dir_path: str, topic: str) -> str
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `dir_path` | 是 | 目录路径，为空则搜根目录。 |
| `topic` | 是 | 你想找什么。支持多个关键词，用逗号分隔，如 `"合同条款, 违约责任, 赔偿"` |

**什么时候用：** 不确定信息在哪个文件里，需要在整个目录中搜索时用。多个文件同时处理，速度快。

```
batch_extract(dir_path="docs", topic="AI")
batch_extract(dir_path="", topic="合同条款, 违约责任, 赔偿")
```

---

## 推荐用法

**场景 1：用户说"找关于 XX 的内容"**
1. 先 `grep_content(keyword="XX")` — 看哪些文件提到了
2. 再 `extract_content(file_path="找到的文件", topic="XX")` — 提取相关段落

**场景 2：用户说"看看这个目录有什么"**
1. `list_directory(path="目录名")` — 列出文件

**场景 3：用户说"读一下某个文件"**
1. 文件小于 500 行 → `read_file(path="文件名")`
2. 文件很大 → `extract_content(file_path="文件名", topic="用户关心的主题")`

**场景 4：用户说"在所有文件中找 XX"**
1. `batch_extract(dir_path="", topic="XX")` — 一次性搜遍所有文件

---

## 注意事项

- 所有路径相对于根目录（通过 `--root` 参数或 `conf/config.yaml` 设置）
- `extract_content` 和 `batch_extract` 用 AI 提取，比 grep 慢但更准确，能理解语义
- PDF、Word 等二进制文件会先转换再搜索
- 文件大小上限 50MB
