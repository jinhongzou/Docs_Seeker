"""
文件系统工具集 — Agent 可调用的 6 个工具

使用 @register_tool 装饰器注册工具，自动收集到全局注册表。
新增工具只需：1) 定义函数  2) 加 @register_tool 装饰器

list_directory  — 列出目录内容
read_file       — 读取文件内容
search_files    — 按文件名搜索
grep_content    — 搜索文件内容
extract_content — LLM 智能提取相关内容
batch_extract   — 批量提取目录下所有文件
"""

import asyncio
import fnmatch
import re
from pathlib import Path
from typing import Callable

from markitdown import MarkItDown

from .utils import resolve_path
from .content_extractor import ContentExtractor
from .config import (
    MAX_FILE_LINES,
    MAX_SNIPPET_LENGTH,
    MAX_CHUNK_LINES,
    MAX_FILE_SIZE_MB,
    MAX_CONTEXT_TOKENS,
)

# 需要 MarkItDown 转换的二进制文件扩展名
BINARY_SEARCH_EXTS = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt"}


# ============================================================
# 工具注册机制
# ============================================================

# 全局工具注册表：{name: (func, description)}
_TOOL_REGISTRY: dict[str, tuple[Callable, str]] = {}


def register_tool(name: str = None, description: str = ""):
    """
    工具注册装饰器。
    
    用法：
        @register_tool("list_directory", "列出目录内容")
        def list_directory(path: str) -> str:
            ...
    
    被装饰的函数会被收集到 _TOOL_REGISTRY，create_tools() 时统一实例化。
    """
    def decorator(func):
        tool_name = name or func.__name__
        _TOOL_REGISTRY[tool_name] = (func, description)
        return func
    return decorator


def get_tool_descriptions() -> str:
    """返回所有已注册工具的描述（用于 prompt）。"""
    lines = []
    for i, (name, (_, desc)) in enumerate(_TOOL_REGISTRY.items(), 1):
        lines.append(f"{i}. {name} — {desc}")
    return "\n".join(lines)


def create_tools(api_key: str, base_url: str, model_name: str, verbose: bool = False) -> dict:
    """
    创建文件系统工具集。
    
    工具函数通过闭包捕获共享的 MarkItDown 和 ContentExtractor 实例。
    使用 @register_tool 装饰器自动注册工具元数据。
    返回: {tool_name: tool_function} 字典
    
    Args:
        verbose: 是否显示工具执行进度（分块处理等）
    """
    md = MarkItDown()
    extractor = ContentExtractor(
        model=model_name,
        api_key=api_key,
        api_base=base_url,
        max_chunk_lines=MAX_CHUNK_LINES,
        max_snippet_length=MAX_SNIPPET_LENGTH,
        max_file_size_mb=MAX_FILE_SIZE_MB,
        maxTokens=MAX_CONTEXT_TOKENS,
    )

    @register_tool("list_directory", "列出目录内容，附带文件大小信息")
    def list_directory(path: str) -> str:
        """列出目录内容（含文件大小）。"""
        try:
            p, note = resolve_path(path)
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
            return (
                prefix
                + f"目录: {p.absolute()}\n共 {len(items)} 个项目\n\n"
                + "\n".join(items)
            )

        except PermissionError:
            return f"[ERROR] 权限不足，无法访问: {path}"
        except Exception as e:
            return f"[ERROR] 列出目录时出错: {str(e)}"

    @register_tool("read_file", "读取文件内容（支持多种格式，带行号）")
    def read_file(
        path: str,
        max_lines: int = MAX_FILE_LINES,
        max_file_size_mb: int = MAX_FILE_SIZE_MB,
    ) -> str:
        """读取文件内容（带行号）。"""
        try:
            p, note = resolve_path(path)
            if not p.exists():
                return f"[ERROR] 文件不存在: {path}"
            if not p.is_file():
                return f"[ERROR] 不是文件: {path}"

            # 文件大小预检
            file_size_mb = p.stat().st_size / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                return (
                    f"[WARN] 文件过大（{file_size_mb:.1f}MB > {max_file_size_mb}MB），"
                    "跳过读取。建议用 extract_content 分块提取。"
                )

            try:
                content = md.convert_local(str(p.absolute())).text_content
            except Exception:
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return f"[WARN] 无法读取: {p.name}"

            lines = content.splitlines()
            if len(lines) > max_lines:
                result = "\n".join(
                    f"{i + 1}: {l}" for i, l in enumerate(lines[:max_lines])
                )
                result += (
                    f"\n\n... (共 {len(lines)} 行，仅显示前 {max_lines} 行，"
                    "可用 extract_content 精确定位)"
                )
                return result

            prefix = f"[{note}]\n" if note else ""
            return prefix + "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines))

        except Exception as e:
            return f"[ERROR] 读取文件时出错: {str(e)}"

    @register_tool("search_files", "递归搜索匹配文件名模式的文件")
    def search_files(pattern: str, root_path: str = ".") -> str:
        """按文件名模式递归搜索。"""
        try:
            root, note = resolve_path(root_path)
            if not root.exists():
                return f"[ERROR] 路径不存在: {root_path}"
            if not root.is_dir():
                return f"[ERROR] 不是目录: {root_path}"

            matches = []
            for f in root.rglob("*"):
                if fnmatch.fnmatch(f.name, pattern):
                    try:
                        rel = str(f.relative_to(root))
                        tag = "[DIR]" if f.is_dir() else "[FILE]"
                        matches.append(f"{tag} {rel}")
                    except ValueError:
                        matches.append(str(f))

            if not matches:
                return f"未找到匹配 '{pattern}' 的文件或目录\n搜索目录: {root_path}"

            matches.sort()
            prefix = f"[{note}]\n" if note else ""
            return (
                prefix
                + f"找到 {len(matches)} 个匹配项（模式: {pattern}）:\n\n"
                + "\n".join(matches)
            )

        except PermissionError:
            return f"[ERROR] 权限不足: {root_path}"
        except Exception as e:
            return f"[ERROR] 搜索时出错: {str(e)}"

    @register_tool("grep_content", "搜索文件内容（grep），返回包含关键词的文件和行号")
    def grep_content(
        keyword: str, root_path: str = ".", file_pattern: str = "*"
    ) -> str:
        """
        搜索文件内容（grep），返回包含关键词的文件和行号。
        对 docx/pdf/xlsx/pptx 等二进制文件自动用 MarkItDown 转换后搜索。

        Args:
            keyword: 搜索关键词（支持正则表达式）
            root_path: 搜索目录
            file_pattern: 文件名过滤模式（默认匹配所有文件）
        """
        try:
            root, note = resolve_path(root_path)
            if not root.exists():
                return f"[ERROR] 路径不存在: {root_path}"
            if not root.is_dir():
                return f"[ERROR] 不是目录: {root_path}"

            # 编译正则（如果用户输入的是纯文本，作为字面量匹配）
            try:
                pattern = re.compile(keyword, re.IGNORECASE)
            except re.error:
                pattern = re.compile(re.escape(keyword), re.IGNORECASE)

            results = []  # [(relative_path, line_num, line_content)]
            files_searched = 0

            try:
                file_iter = root.rglob(file_pattern)
            except re.error:
                return f"[ERROR] 无效的文件模式: {file_pattern}"
            except Exception:
                file_iter = root.rglob("*")

            for f in file_iter:
                if not f.is_file():
                    continue
                # 跳过隐藏目录和无意义目录
                if any(
                    (part.startswith(".") and part != ".")
                    or part in ("__pycache__", ".git", ".venv", "node_modules")
                    for part in f.relative_to(root).parts[:-1]
                ):
                    continue
                # 跳过超大文件
                try:
                    if f.stat().st_size > MAX_FILE_SIZE_MB * 1_000_000:
                        continue
                except OSError:
                    continue

                try:
                    rel = str(f.relative_to(root).as_posix())
                    ext = f.suffix.lower()

                    if ext in BINARY_SEARCH_EXTS:
                        # 二进制文件：MarkItDown 转换后搜索
                        try:
                            result = md.convert(str(f))
                            text = (
                                result.text_content
                                if hasattr(result, "text_content")
                                else str(result)
                            )
                            files_searched += 1
                            for line_num, line in enumerate(text.splitlines(), 1):
                                if pattern.search(line):
                                    results.append(
                                        (rel, line_num, line.rstrip()[:200])
                                    )
                        except Exception:
                            continue
                    else:
                        # 文本文件：直接读取
                        with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                            files_searched += 1
                            for line_num, line in enumerate(fh, 1):
                                if pattern.search(line):
                                    results.append(
                                        (rel, line_num, line.rstrip()[:200])
                                    )
                except (PermissionError, OSError):
                    continue

            if not results:
                return (
                    f"未找到包含 '{keyword}' 的内容\n"
                    f"搜索目录: {root_path}, 文件模式: {file_pattern}, "
                    f"已搜索 {files_searched} 个文件"
                )

            prefix = f"[{note}]\n" if note else ""
            lines = [
                prefix
                + f"找到 {len(results)} 处匹配（关键词: {keyword}，"
                f"已搜索 {files_searched} 个文件）:\n"
            ]
            for rel_path, line_num, line_content in results[:50]:
                lines.append(f"  {rel_path}:{line_num}: {line_content}")
            if len(results) > 50:
                lines.append(f"\n  ... (共 {len(results)} 处匹配，仅显示前 50 条)")

            return "\n".join(lines)

        except PermissionError:
            return f"[ERROR] 权限不足: {root_path}"
        except Exception as e:
            return f"[ERROR] 内容搜索时出错: {str(e)}"

    @register_tool("extract_content", "从文件中提取与主题相关的内容段落")
    async def extract_content(file_path: str, topic: str) -> str:
        """从文件中提取与主题相关的内容段落（异步，分块时并行）。"""
        try:
            p, _ = resolve_path(file_path)
            if not p.exists():
                return f"[ERROR] 文件不存在: {file_path}"

            def _progress(msg):
                if verbose:
                    print(f"  [extract_content] {msg}")

            result = await extractor.async_scan(
                file_path, topic=topic, progress_callback=_progress
            )
            sources = result.get("sources_gathered", [])
            error = result.get("error")

            if error and not sources:
                return (
                    f"[FILE] {p.name}\n主题: {topic}\n"
                    f"[ERROR] LLM 提取出错: {error}"
                )

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
                    f"行 {src['start_line']}-{src['end_line']}: "
                    f"{src.get('reasoning', '')}\n"
                    f"内容: {snippet}\n"
                )

            return "\n\n".join(parts)

        except Exception as e:
            return f"[ERROR] 内容提取时出错: {str(e)}"

    @register_tool("batch_extract", "批量提取目录下所有文件中与主题相关的内容")
    async def batch_extract(dir_path: str, topic: str) -> str:
        """批量提取目录下所有文件中与主题相关的内容（异步并行）。"""
        try:
            p, note = resolve_path(dir_path)
            if not p.exists():
                return f"[ERROR] 路径不存在: {dir_path}"
            if not p.is_dir():
                return f"[ERROR] 不是目录: {dir_path}"

            supported_exts = {
                ".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".doc",
                ".pptx", ".ppt", ".xlsx", ".xls",
            }
            files = [
                f for f in sorted(p.rglob("*"))
                if f.is_file() and f.suffix.lower() in supported_exts
            ]

            if not files:
                return f"[ERROR] 目录下没有支持的文件: {dir_path}"

            header = (
                f"[BATCH] 目录: {dir_path}\n"
                f"主题: {topic}\n"
                f"文件数: {len(files)}\n"
            )

            async def _process_file(i: int, f: Path) -> tuple:
                """并行处理单个文件，返回 (lines, is_success)。"""
                rel = str(f.relative_to(p)).replace("\\", "/")
                try:
                    def _progress(msg):
                        if verbose:
                            print(f"  [batch_extract] [{i}/{len(files)}] {rel}: {msg}")

                    result = await extractor.async_scan(
                        str(f), topic=topic, progress_callback=_progress
                    )
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
                    return ([f"--- 文件 {i}: {rel} [ERROR] ---\n{str(e)}\n"], False)

            # 并行处理所有文件
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
            return f"[ERROR] 批量提取时出错: {str(e)}"

    # 从注册表构建工具字典（装饰器已自动注册）
    return {name: func for name, (func, _) in _TOOL_REGISTRY.items()}
