"""
DocSeeker MCP Server — 将文件系统探索工具暴露为 MCP 服务

供 Claude Desktop、OpenCode 等 AI Agent 调用。

启动方式:
    python cli.py --mcp                         # 默认目录 SeekerDocs
    python cli.py --mcp --root /path/to/docs    # 指定目录
    DOCEEKER_ROOT=/docs python cli.py --mcp     # 环境变量指定目录
"""

import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from markitdown import MarkItDown

from .utils import resolve_path
from .content_extractor import ContentExtractor
from .config import MAX_FILE_LINES, MAX_SNIPPET_LENGTH, MAX_CHUNK_LINES, MAX_FILE_SIZE_MB, MAX_CONTEXT_TOKENS

# 需要 MarkItDown 转换的二进制文件扩展名
BINARY_SEARCH_EXTS = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt"}

# 模块级状态，init_mcp_server() 时设置
_md: MarkItDown = None
_extractor: ContentExtractor = None
_root_path: str = "SeekerDocs"


def init_mcp_server(
    root_path: str = "SeekerDocs",
    api_key: str = "",
    model_name: str = "Qwen/Qwen2.5-7B-Instruct",
    base_url: str = "https://api.siliconflow.cn/v1",
):
    """初始化共享实例（在创建 MCP server 前调用）。"""
    global _md, _extractor, _root_path
    _md = MarkItDown()
    _extractor = ContentExtractor(
        model=model_name,
        api_key=api_key,
        api_base=base_url,
        max_chunk_lines=MAX_CHUNK_LINES,
        max_snippet_length=MAX_SNIPPET_LENGTH,
        max_file_size_mb=MAX_FILE_SIZE_MB,
        maxTokens=MAX_CONTEXT_TOKENS,
    )
    _root_path = root_path


mcp = FastMCP(
    name="DocSeeker",
    instructions=(
        "DocSeeker 文件系统探索工具集。所有路径均为相对于根目录的相对路径。\n"
        "可用工具：\n"
        "- list_directory(path): 列出目录内容\n"
        "- read_file(path, max_lines): 读取文件内容\n"
        "- search_files(pattern, root_path): 按文件名模式搜索\n"
        "- grep_content(keyword, root_path, file_pattern): 搜索文件内容\n"
        "- extract_content(file_path, topic): 用 LLM 从单个文件提取相关内容\n"
        "- batch_extract(dir_path, topic): 批量提取目录下所有文件\n"
        "注意：extract_content 的参数是 file_path 和 topic，不是 path 和 query。"
    ),
)


@mcp.tool()
def list_directory(
    path: Annotated[str, Field(description="相对于根目录的目录路径，例如 'subdir' 或 '.' 表示根目录")] = ".",
) -> str:
    """列出目录内容，附带文件大小信息。返回文件名、大小和类型。"""
    try:
        p, note = resolve_path(path, _root_path)
        if not p.exists():
            return f"[ERROR] 路径不存在: {path}"
        if not p.is_dir():
            return f"[ERROR] 不是目录: {path}"

        items = []
        for item in sorted(p.iterdir()):
            if item.is_dir():
                items.append(f"[DIR] {item.name}/")
            else:
                size = item.stat().st_size
                if size < 1024:
                    s = f"{size} B"
                elif size < 1024 * 1024:
                    s = f"{size / 1024:.1f} KB"
                else:
                    s = f"{size / 1024 / 1024:.1f} MB"
                items.append(f"[FILE] {item.name} ({s})")

        if not items:
            return "（空目录）" + (f"\n[{note}]" if note else "")

        prefix = f"[{note}]\n" if note else ""
        return prefix + f"目录: {p.absolute()}\n共 {len(items)} 个项目\n\n" + "\n".join(items)

    except PermissionError:
        return f"[ERROR] 权限不足: {path}"
    except Exception as e:
        return f"[ERROR] 列出目录时出错: {e}"


@mcp.tool()
def read_file(
    path: Annotated[str, Field(description="相对于根目录的文件路径，例如 'report.docx' 或 'subdir/data.csv'")],
    max_lines: Annotated[int, Field(description="最多读取的行数，默认 500 行")] = MAX_FILE_LINES,
) -> str:
    """读取文件内容（支持 docx/pdf/xlsx/pptx/txt/md/csv 等），带行号输出。"""
    try:
        p, note = resolve_path(path, _root_path)
        if not p.exists():
            return f"[ERROR] 文件不存在: {path}"
        if not p.is_file():
            return f"[ERROR] 不是文件: {path}"

        file_size_mb = p.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            return (
                f"[WARN] 文件过大（{file_size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB），"
                "建议用 extract_content 分块提取。"
            )

        try:
            content = _md.convert_local(str(p.absolute())).text_content
        except Exception:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return f"[WARN] 无法读取: {p.name}"

        lines = content.splitlines()
        if len(lines) > max_lines:
            result = "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines[:max_lines]))
            result += f"\n\n... (共 {len(lines)} 行，仅显示前 {max_lines} 行，可用 extract_content 精确定位)"
            return result

        prefix = f"[{note}]\n" if note else ""
        return prefix + "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines))

    except Exception as e:
        return f"[ERROR] 读取文件时出错: {e}"


@mcp.tool()
def search_files(
    pattern: Annotated[str, Field(description="文件名匹配模式（glob），例如 '*.pdf'、'*.docx'、'report*'")],
    root_path: Annotated[str, Field(description="搜索起始目录，为空则使用默认根目录")] = "",
) -> str:
    """递归搜索匹配文件名模式的文件。返回所有匹配的文件路径列表。"""
    try:
        search_root = Path(root_path) if root_path else Path(_root_path)
        if not search_root.exists():
            return f"[ERROR] 路径不存在: {root_path or _root_path}"
        if not search_root.is_dir():
            return f"[ERROR] 不是目录: {root_path or _root_path}"

        matches = []
        for f in search_root.rglob("*"):
            if fnmatch.fnmatch(f.name, pattern):
                try:
                    rel = str(f.relative_to(search_root))
                    tag = "[DIR]" if f.is_dir() else "[FILE]"
                    matches.append(f"{tag} {rel}")
                except ValueError:
                    matches.append(str(f))

        if not matches:
            return f"未找到匹配 '{pattern}' 的文件或目录\n搜索目录: {root_path or _root_path}"

        matches.sort()
        return f"找到 {len(matches)} 个匹配项（模式: {pattern}）:\n\n" + "\n".join(matches)

    except PermissionError:
        return f"[ERROR] 权限不足: {root_path or _root_path}"
    except Exception as e:
        return f"[ERROR] 搜索时出错: {e}"


@mcp.tool()
def grep_content(
    keyword: Annotated[str, Field(description="搜索关键词或正则表达式，例如 '贷款'、'risk.*analysis'")],
    root_path: Annotated[str, Field(description="搜索起始目录，为空则使用默认根目录")] = "",
    file_pattern: Annotated[str, Field(description="只搜索匹配此模式的文件，例如 '*.md'、'*.txt'，默认搜索全部")] = "*",
) -> str:
    """搜索文件内容（grep），返回包含关键词的文件名、行号和匹配行。支持正则表达式。"""
    try:
        search_root = Path(root_path) if root_path else Path(_root_path)
        if not search_root.exists():
            return f"[ERROR] 路径不存在: {root_path or _root_path}"
        if not search_root.is_dir():
            return f"[ERROR] 不是目录: {root_path or _root_path}"

        try:
            pat = re.compile(keyword, re.IGNORECASE)
        except re.error:
            pat = re.compile(re.escape(keyword), re.IGNORECASE)

        results = []
        files_searched = 0

        try:
            file_iter = search_root.rglob(file_pattern)
        except Exception:
            file_iter = search_root.rglob("*")

        for f in file_iter:
            if not f.is_file():
                continue
            if any(
                (part.startswith(".") and part != ".")
                or part in ("__pycache__", ".git", ".venv", "node_modules")
                for part in f.relative_to(search_root).parts[:-1]
            ):
                continue
            try:
                if f.stat().st_size > MAX_FILE_SIZE_MB * 1_000_000:
                    continue
            except OSError:
                continue

            try:
                rel = str(f.relative_to(search_root).as_posix())
                ext = f.suffix.lower()

                if ext in BINARY_SEARCH_EXTS:
                    try:
                        result = _md.convert(str(f))
                        text = result.text_content if hasattr(result, "text_content") else str(result)
                        files_searched += 1
                        for line_num, line in enumerate(text.splitlines(), 1):
                            if pat.search(line):
                                results.append((rel, line_num, line.rstrip()[:200]))
                    except Exception:
                        continue
                else:
                    with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                        files_searched += 1
                        for line_num, line in enumerate(fh, 1):
                            if pat.search(line):
                                results.append((rel, line_num, line.rstrip()[:200]))
            except (PermissionError, OSError):
                continue

        if not results:
            return (
                f"未找到包含 '{keyword}' 的内容\n"
                f"搜索目录: {root_path or _root_path}, 文件模式: {file_pattern}, "
                f"已搜索 {files_searched} 个文件"
            )

        lines = [
            f"找到 {len(results)} 处匹配（关键词: {keyword}，已搜索 {files_searched} 个文件）:\n"
        ]
        for rel_path, line_num, line_content in results[:50]:
            lines.append(f"  {rel_path}:{line_num}: {line_content}")
        if len(results) > 50:
            lines.append(f"\n  ... (共 {len(results)} 处匹配，仅显示前 50 条)")

        return "\n".join(lines)

    except PermissionError:
        return f"[ERROR] 权限不足: {root_path or _root_path}"
    except Exception as e:
        return f"[ERROR] 内容搜索时出错: {e}"


@mcp.tool()
async def extract_content(
    file_path: Annotated[str, Field(description="要提取内容的文件路径（相对于根目录），例如 'report.docx'")],
    topic: Annotated[str, Field(description="要提取的主题或关键词，例如 '贷款风险分析'、'营收数据'")],
) -> str:
    """从单个文件中用 LLM 智能提取与主题相关的内容段落。适合精准定位大文件中的特定信息。"""
    try:
        p, _ = resolve_path(file_path, _root_path)
        if not p.exists():
            return f"[ERROR] 文件不存在: {file_path}"

        result = await _extractor.async_scan(str(p), topic=topic)
        sources = result.get("sources_gathered", [])
        error = result.get("error")

        if error and not sources:
            return f"[FILE] {p.name}\n主题: {topic}\n[ERROR] LLM 提取出错: {error}"
        if not sources:
            return f"[FILE] {p.name}\n主题: {topic}\n[RESULT] 未找到相关内容"

        parts = [
            f"[FILE] {file_path}",
            f"主题: {topic}",
            f"[RESULT] 找到 {len(sources)} 处匹配:\n",
        ]
        for i, src in enumerate(sources, 1):
            snippet = src.get("relevant_content", "")
            if len(snippet) > MAX_SNIPPET_LENGTH:
                snippet = snippet[:MAX_SNIPPET_LENGTH] + "\n...（截断）"
            parts.append(
                f"--- 匹配 {i} ---\n"
                f"行 {src['start_line']}-{src['end_line']}: {src.get('reasoning', '')}\n"
                f"内容: {snippet}\n"
            )

        return "\n\n".join(parts)

    except Exception as e:
        return f"[ERROR] 内容提取时出错: {e}"


@mcp.tool()
async def batch_extract(
    dir_path: Annotated[str, Field(description="要批量提取的目录路径（相对于根目录），为空则使用根目录")],
    topic: Annotated[str, Field(description="要提取的主题或关键词，例如 '合同条款'、'技术架构'")],
) -> str:
    """批量提取目录下所有文件中与主题相关的内容（异步并行处理）。返回每个文件的匹配结果。"""
    try:
        p = Path(dir_path) if dir_path else Path(_root_path)
        if not p.exists():
            return f"[ERROR] 路径不存在: {dir_path or _root_path}"
        if not p.is_dir():
            return f"[ERROR] 不是目录: {dir_path or _root_path}"

        supported_exts = {
            ".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".doc",
            ".pptx", ".ppt", ".xlsx", ".xls",
        }
        files = [f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix.lower() in supported_exts]

        if not files:
            return f"[ERROR] 目录下没有支持的文件: {dir_path or _root_path}"

        header = f"[BATCH] 目录: {dir_path or _root_path}\n主题: {topic}\n文件数: {len(files)}\n"

        async def _process_file(i: int, f: Path) -> tuple:
            rel = str(f.relative_to(p)).replace("\\", "/")
            try:
                result = await _extractor.async_scan(str(f), topic=topic)
                sources = result.get("sources_gathered", [])
                error = result.get("error")

                if error and not sources:
                    return ([f"--- 文件 {i}: {rel} [ERROR] ---\n{error}\n"], False)
                if not sources:
                    return ([f"--- 文件 {i}: {rel} [无匹配] ---\n"], True)

                lines = [f"--- 文件 {i}: {rel} [匹配 {len(sources)} 处] ---"]
                for j, src in enumerate(sources, 1):
                    snippet = src.get("relevant_content", "")
                    if len(snippet) > MAX_SNIPPET_LENGTH:
                        snippet = snippet[:MAX_SNIPPET_LENGTH] + "\n...（截断）"
                    lines.append(
                        f"  匹配 {j} (第 {src['start_line']}-{src['end_line']} 行):\n"
                        f"  原因: {src['reasoning']}\n"
                        f"  内容: {snippet}\n"
                    )
                return (lines, True)
            except Exception as e:
                return ([f"--- 文件 {i}: {rel} [ERROR] ---\n{e}\n"], False)

        file_tasks = [_process_file(i, f) for i, f in enumerate(files, 1)]
        file_results = await asyncio.gather(*file_tasks)

        parts = [header]
        success_count = 0
        error_count = 0
        for lines, is_success in file_results:
            parts.extend(lines)
            if is_success:
                success_count += 1
            else:
                error_count += 1

        parts.insert(1, f"[RESULT] 成功: {success_count}, 失败: {error_count}\n")
        return "\n".join(parts)

    except Exception as e:
        return f"[ERROR] 批量提取时出错: {e}"


def run_mcp_server(transport: str = "stdio"):
    """启动 MCP 服务器。

    Args:
        transport: 传输方式，"stdio"（Claude Desktop）或 "sse"（HTTP）
    """
    mcp.run(transport=transport)
