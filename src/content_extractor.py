"""文件内容提取器（MarkItDown + LLM 结构化抽取，支持大文件分块）"""

import os
import hashlib
import mimetypes
import asyncio
import logging
from typing import Dict, Optional, List

from markitdown import MarkItDown
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from .schemas import ExtractResult
from .prompts import EXTRACT_PROMPT
from .utils import estimate_tokens, truncate_to_tokens

logger = logging.getLogger(__name__)


class ContentExtractor:
    def __init__(self, model: str, api_key: str, api_base: str,
                 max_chunk_lines: int = 500, max_snippet_length: int = 600,
                 max_file_size_mb: int = 50,
                 maxTokens: int = 32768) -> None:
        self.llm = ChatOpenAI(
            model=model,
            temperature=0.0,
            max_retries=2,
            openai_api_key=api_key,
            openai_api_base=api_base,
        )
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", EXTRACT_PROMPT),
            ("human", "[ ## Context ## ]\n行号:内容\n---{file_content}"),
        ])
        self._chain = self._prompt | self.llm.with_structured_output(
            ExtractResult, method="function_calling"
        )
        self._md = MarkItDown()
        self._max_chunk_lines = max_chunk_lines
        self._max_snippet_length = max_snippet_length
        self._max_file_size_mb = max_file_size_mb
        self._maxTokens = maxTokens
        self._cache: dict[tuple, dict] = {}  # (file_path, topic) -> scan 结果
        self._cache_max_size = 50

    def scan(self, file_path: str, topic: str = None, progress_callback=None) -> dict:
        """
        分析单个文件，提取与主题相关的段落。

        大文件（超过 max_chunk_lines 行）会自动分块处理：
        - 每块独立调用 LLM 提取
        - 合并去重后返回

        Args:
            progress_callback: 可选的进度回调函数 callback(message: str)
        """
        # 检查缓存
        cache_key = (os.path.realpath(file_path), topic)
        if cache_key in self._cache:
            if progress_callback:
                progress_callback(f"使用缓存: {os.path.basename(file_path)}")
            return self._cache[cache_key]

        path = os.path.realpath(file_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_info = self.read_file(path)
        if not file_info or "content" not in file_info:
            raise ValueError(f"无法读取文件内容: {file_path}")

        content = file_info["content"]
        all_lines = content.splitlines()
        total_lines = len(all_lines)

        # 小文件：单块处理（保持原有行为）
        if total_lines <= self._max_chunk_lines:
            if progress_callback:
                progress_callback(f"文件共 {total_lines} 行，单块处理")
            result = self._scan_single_chunk(file_path, content, topic)
        else:
            # 大文件：分块处理
            total_chunks = (total_lines + self._max_chunk_lines - 1) // self._max_chunk_lines
            if progress_callback:
                progress_callback(f"文件共 {total_lines} 行，分 {total_chunks} 块处理（每块 {self._max_chunk_lines} 行）")
            logger.info("文件 %s 共 %d 行，分块处理（每块 %d 行）",
                         file_path, total_lines, self._max_chunk_lines)
            result = self._scan_chunked(file_path, all_lines, topic, progress_callback)

        # 存入缓存（LRU: 超过上限时删除最早的）
        if len(self._cache) >= self._cache_max_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = result

        return result

    async def async_scan(self, file_path: str, topic: str = None, progress_callback=None) -> dict:
        """异步版 scan，分块时并行调用 LLM 提取。"""
        cache_key = (os.path.realpath(file_path), topic)
        if cache_key in self._cache:
            if progress_callback:
                progress_callback(f"使用缓存: {os.path.basename(file_path)}")
            return self._cache[cache_key]

        path = os.path.realpath(file_path)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_info = self.read_file(path)
        if not file_info or "content" not in file_info:
            raise ValueError(f"无法读取文件内容: {file_path}")

        content = file_info["content"]
        all_lines = content.splitlines()
        total_lines = len(all_lines)

        if total_lines <= self._max_chunk_lines:
            if progress_callback:
                progress_callback(f"文件共 {total_lines} 行，单块处理")
            result = self._scan_single_chunk(file_path, content, topic)
        else:
            total_chunks = (total_lines + self._max_chunk_lines - 1) // self._max_chunk_lines
            if progress_callback:
                progress_callback(f"文件共 {total_lines} 行，并行 {total_chunks} 块")
            logger.info("文件 %s 共 %d 行，并行分块（每块 %d 行）",
                         file_path, total_lines, self._max_chunk_lines)
            result = await self._async_scan_chunked(file_path, all_lines, topic, progress_callback)

        if len(self._cache) >= self._cache_max_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = result

        return result

    def _scan_single_chunk(self, file_path: str, content: str, topic: str) -> dict:
        """单块处理：直接调用 LLM 提取。"""
        context = (
            f"[ ## 当前访问文件位置 ## ]\n{file_path}\n\n"
            f"[ ## context ## ]\n行号:内容---\n"
            f"{self._add_line_numbers(content)}"
        )

        result, error = self._extract(file_content=context, topic=topic)
        sources = self._build_sources(file_path, content, result)

        if error:
            logger.warning("LLM 提取出错（文件 %s）: %s", file_path, error)

        return {
            "sources_gathered": sources,
            "search_query": [context],
            "web_research_result": [seg.reasoning for seg in result],
            "error": error,
        }

    def _scan_chunked(self, file_path: str, all_lines: List[str], topic: str,
                      progress_callback=None) -> dict:
        """
        分块处理大文件：
        1. 按 max_chunk_lines 分块
        2. 每块独立调 LLM 提取
        3. 合并去重（基于行号范围重叠判断）
        """
        all_segments = []  # (start_line, end_line, reasoning, content)
        errors = []  # 收集各块错误
        total_lines = len(all_lines)
        total_chunks = (total_lines + self._max_chunk_lines - 1) // self._max_chunk_lines

        for chunk_start in range(0, total_lines, self._max_chunk_lines):
            chunk_end = min(chunk_start + self._max_chunk_lines, total_lines)
            chunk_lines = all_lines[chunk_start:chunk_end]

            chunk_num = chunk_start // self._max_chunk_lines + 1
            if progress_callback:
                progress_callback(f"正在处理第 {chunk_num}/{total_chunks} 块（行 {chunk_start + 1}-{chunk_end}）")
            logger.debug("处理块 %d/%d（行 %d-%d）", chunk_num, total_chunks, chunk_start + 1, chunk_end)

            # 构建带偏移行号的上下文（LLM 看到的是全局行号）
            numbered_lines = []
            for i, line in enumerate(chunk_lines):
                numbered_lines.append(f"{chunk_start + i + 1}: {line}")
            context = (
                f"[ ## 当前访问文件位置 ## ]\n{file_path}\n"
                f"[ ## 行号范围 ## ]\n{chunk_start + 1} ~ {chunk_end}\n\n"
                f"[ ## context ## ]\n行号:内容---\n"
                + "\n".join(numbered_lines)
            )

            try:
                result, error = self._extract(file_content=context, topic=topic)
                if error:
                    logger.warning("块 %d/%d LLM 提取出错: %s", chunk_num, total_chunks, error)
                    errors.append(f"块{chunk_num}: {error}")
                full_content = "\n".join(all_lines)
                for seg in result:
                    start = seg.start_line
                    end = seg.end_line
                    snippet = self._get_lines_by_range(full_content, start, end)
                    all_segments.append((start, end, seg.reasoning, snippet))
            except Exception as e:
                logger.warning("块 %d/%d LLM 提取失败: %s", chunk_num, total_chunks, e)
                errors.append(f"块{chunk_num}: {e}")
                continue

        # 去重合并：合并重叠或相邻的段落
        merged = self._merge_segments(all_segments)

        sources = []
        for start, end, reasoning, snippet in merged:
            sources.append({
                "file_path": file_path,
                "start_line": start,
                "end_line": end,
                "reasoning": reasoning,
                "relevant_content": snippet,
            })

        return {
            "sources_gathered": sources,
            "search_query": [f"[分块处理] {file_path} ({total_lines} 行)"],
            "web_research_result": [s[2] for s in merged],
            "error": "; ".join(errors) if errors else None,
        }

    async def _async_scan_chunked(self, file_path: str, all_lines: list, topic: str,
                                   progress_callback=None) -> dict:
        """异步并行分块处理：所有块同时调 LLM 提取，用 asyncio.gather 并行化。"""
        total_lines = len(all_lines)
        total_chunks = (total_lines + self._max_chunk_lines - 1) // self._max_chunk_lines

        async def _process_chunk(chunk_start: int) -> tuple:
            """处理单个块，返回 (segments, error_msg)。"""
            chunk_end = min(chunk_start + self._max_chunk_lines, total_lines)
            chunk_lines = all_lines[chunk_start:chunk_end]
            chunk_num = chunk_start // self._max_chunk_lines + 1

            numbered_lines = [
                f"{chunk_start + i + 1}: {line}"
                for i, line in enumerate(chunk_lines)
            ]
            context = (
                f"[ ## 当前访问文件位置 ## ]\n{file_path}\n"
                f"[ ## 行号范围 ## ]\n{chunk_start + 1} ~ {chunk_end}\n\n"
                f"[ ## context ## ]\n行号:内容---\n"
                + "\n".join(numbered_lines)
            )

            if progress_callback:
                progress_callback(f"第 {chunk_num}/{total_chunks} 块（行 {chunk_start + 1}-{chunk_end}）")

            try:
                result, error = await self._extract_async(file_content=context, topic=topic)
                if error:
                    return [], f"块{chunk_num}: {error}"
                full_content = "\n".join(all_lines)
                segments = []
                for seg in result:
                    snippet = self._get_lines_by_range(full_content, seg.start_line, seg.end_line)
                    segments.append((seg.start_line, seg.end_line, seg.reasoning, snippet))
                return segments, None
            except Exception as e:
                return [], f"块{chunk_num}: {e}"

        # 并行执行所有块
        chunks = list(range(0, total_lines, self._max_chunk_lines))
        results = await asyncio.gather(*[_process_chunk(cs) for cs in chunks])

        all_segments = []
        errors = []
        for segments, error in results:
            all_segments.extend(segments)
            if error:
                errors.append(error)

        merged = self._merge_segments(all_segments)

        sources = [
            {
                "file_path": file_path,
                "start_line": start,
                "end_line": end,
                "reasoning": reasoning,
                "relevant_content": snippet,
            }
            for start, end, reasoning, snippet in merged
        ]

        return {
            "sources_gathered": sources,
            "search_query": [f"[并行分块] {file_path} ({total_lines} 行)"],
            "web_research_result": [s[2] for s in merged],
            "error": "; ".join(errors) if errors else None,
        }

    def _merge_segments(self, segments: list) -> list:
        """合并重叠或相邻的段落，去重。"""
        if not segments:
            return []

        # 按起始行号排序
        sorted_segs = sorted(segments, key=lambda x: x[0])
        merged = [sorted_segs[0]]

        for start, end, reasoning, snippet in sorted_segs[1:]:
            prev_start, prev_end, prev_reasoning, prev_snippet = merged[-1]
            # 如果当前段落与上一段落重叠或相邻（间隔 ≤5 行），合并
            if start <= prev_end + 5:
                new_end = max(end, prev_end)
                new_snippet = prev_snippet + "\n...\n" + snippet
                merged[-1] = (prev_start, new_end, prev_reasoning + " | " + reasoning, new_snippet)
            else:
                merged.append((start, end, reasoning, snippet))

        return merged

    def _build_sources(self, file_path: str, content: str, result) -> list:
        """从 LLM 结果构建 sources 列表。"""
        sources = []
        for seg in result:
            snippet = self._get_lines_by_range(content, seg.start_line, seg.end_line)
            sources.append({
                "file_path": file_path,
                "start_line": seg.start_line,
                "end_line": seg.end_line,
                "reasoning": seg.reasoning,
                "relevant_content": snippet,
            })
        return sources

    def _convert_with_timeout(self, path: str, timeout: int = 30) -> Optional[str]:
        """带超时的 MarkItDown 转换。"""
        import concurrent.futures

        def _convert():
            return self._md.convert_local(path).text_content

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_convert)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("MarkItDown 转换超时（%ds）: %s", timeout, path)
                return None
            except Exception as e:
                logger.warning("MarkItDown 转换失败: %s", e)
                return None

    def read_file(self, path: str) -> Optional[Dict]:
        """读取文件内容（MarkItDown 全量格式，回退纯文本）。带文件大小预检和超时。"""
        if not os.path.isfile(path):
            logger.warning("文件路径不存在: %s", path)
            return None

        # 文件大小预检
        file_size = os.path.getsize(path)
        file_size_mb = file_size / (1024 * 1024)
        if file_size_mb > self._max_file_size_mb:
            logger.warning("文件过大（%.1fMB > %dMB），跳过 MarkItDown 转换: %s",
                           file_size_mb, self._max_file_size_mb, path)
            # 超大文件直接尝试纯文本读取
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                return None
        else:
            # 根据文件大小动态调整超时
            timeout = 10 if file_size_mb < 1 else 30 if file_size_mb < 5 else 60
            content = self._convert_with_timeout(path, timeout=timeout)

            if content is None:
                # 回退到纯文本读取
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    logger.error("无法读取文件内容: %s", e)
                    return None

        mime_type, _ = mimetypes.guess_type(path)
        file_type = mime_type or os.path.splitext(path)[1][1:].lower() or "unknown"

        return {
            "file_path": path,
            "file_hash": hashlib.md5(path.encode("utf-8")).hexdigest(),
            "content": content,
            "file_name": os.path.basename(path),
            "file_type": file_type,
            "file_size": os.path.getsize(path),
        }

    def _extract(self, file_content: str, topic: str) -> tuple:
        """
        调用 LLM 提取与主题相关的段落。

        Returns:
            (segments, error_msg): segments 是提取结果列表，error_msg 是错误信息（无错误时为 None）
        """
        # token 保护：截断过长的文件内容
        est = estimate_tokens(file_content)
        if est > self._maxTokens - 500:
            file_content = truncate_to_tokens(file_content, self._maxTokens - 500)

        try:
            result = self._chain.invoke({
                "research_topic": topic,
                "file_content": file_content,
            })
            if result is None:
                return ([], None)
            return ([seg for seg in result.matches], None)
        except Exception as e:
            logger.warning("LLM 提取失败: %s", e)
            return ([], str(e))

    async def _extract_async(self, file_content: str, topic: str) -> tuple:
        """异步版 _extract，使用 ainvoke 实现非阻塞 LLM 调用。"""
        # token 保护：截断过长的文件内容
        est = estimate_tokens(file_content)
        if est > self._maxTokens - 500:
            file_content = truncate_to_tokens(file_content, self._maxTokens - 500)

        try:
            result = await self._chain.ainvoke({
                "research_topic": topic,
                "file_content": file_content,
            })
            if result is None:
                return ([], None)
            return ([seg for seg in result.matches], None)
        except Exception as e:
            logger.warning("LLM 异步提取失败: %s", e)
            return ([], str(e))

    @staticmethod
    def _add_line_numbers(text: str) -> str:
        lines = text.splitlines()
        return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))

    @staticmethod
    def _get_lines_by_range(text: str, start_line: int, end_line: int) -> str:
        lines = text.splitlines()
        return "\n".join(lines[start_line - 1 : end_line])
