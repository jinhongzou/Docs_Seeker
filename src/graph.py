"""
文件系统探索智能体 — 纯文本 ReAct 感知-决策-执行循环

架构:
   用户自然语言 → ①任务解析 → ②环境感知(工具) → ③决策规划 → ④执行工具
                                            ↖____________________↙
                                            迭代循环直到任务完成

输出格式:
   ACTION: tool_name(arg1="val1", arg2="val2")   # 调用工具
   ANSWER: <最终答案>                              # 给出回答
   ---OBSERVATION---                               # 工具返回结果

模块拆分:
   utils.py  — 路径解析、文本提取、对话截断等通用函数
   tools.py  — 文件系统工具定义（list_directory 等 6 个工具）
   graph.py  — LangGraph 工作流（本文件）
"""

import asyncio
import hashlib
import inspect
import json
import logging
import re
from pathlib import Path
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import START, END, StateGraph

from .state import ExplorerState
from .utils import (
    debug_messages,
    estimate_tokens,
    has_answer,
    parse_action,
    parse_path_from_text,
    resolve_path,
    truncate_conversation,
    truncate_to_tokens,
)
from .tools import create_tools
from .prompts import AGENT_SYSTEM_PROMPT, PRESCAN_PROMPT
from .config import (
    DEFAULT_ROOT_PATH,
    MAX_CONTEXT_TOKENS,
    MAX_EXPLORATION_ITERATIONS,
    MAX_FILE_LINES,
    MAX_FILE_SIZE_MB,
    MAX_STEPS,
    MAX_CHUNK_LINES,
    MAX_SNIPPET_LENGTH,
    MAX_CONTEXT_ROUNDS,
    MAX_TREE_DEPTH,
    MAX_TREE_FILES,
)

# 跳过的目录/文件名
_SKIP_NAMES = frozenset({
    ".", "__pycache__", ".git", ".venv", "node_modules",
    ".env", ".idea", ".vscode", ".DS_Store", "Thumbs.db",
})

logger = logging.getLogger(__name__)


# ============================================================
# 预扫描：文件树 + LLM 候选筛选 + 缓存
# ============================================================

# 预扫描缓存目录
_PRESCAN_CACHE_DIR = Path("sessions") / ".prescan_cache"


def _get_dir_hash(root_path: str, max_depth: int) -> str:
    """
    计算目录结构的哈希值（基于文件路径和修改时间）。
    用于缓存预扫描结果——目录内容不变时跳过 LLM 调用。
    """
    root = Path(root_path)
    if not root.exists():
        return ""
    
    parts = []
    
    def _walk(dir_path: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        
        for entry in entries:
            if entry.name in _SKIP_NAMES:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if entry.is_dir():
                parts.append(f"D:{entry.name}:{mtime:.0f}")
                _walk(entry, depth + 1)
            else:
                size = entry.stat().st_size
                parts.append(f"F:{entry.name}:{size}:{mtime:.0f}")
    
    _walk(root, 0)
    return hashlib.md5("\n".join(parts).encode()).hexdigest()[:16]


def _load_prescan_cache(dir_hash: str, topic: str) -> list | None:
    """从缓存加载预扫描结果。"""
    if not dir_hash:
        return None
    cache_file = _PRESCAN_CACHE_DIR / f"{dir_hash}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data.get(topic)
    except (json.JSONDecodeError, OSError):
        return None


def _save_prescan_cache(dir_hash: str, topic: str, candidates: list):
    """保存预扫描结果到缓存。"""
    if not dir_hash:
        return
    _PRESCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _PRESCAN_CACHE_DIR / f"{dir_hash}.json"
    
    # 读取已有缓存（可能有多个 topic）
    data = {}
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    
    data[topic] = candidates
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_file_tree(root_path: str, max_depth: int = MAX_TREE_DEPTH) -> tuple:
    """
    递归扫描目录，返回 (文件树字符串, 所有文件路径列表)。

    不限制文件数量（由 _prescan 分批处理），只限制递归深度。
    """
    root = Path(root_path)
    if not root.exists() or not root.is_dir():
        return (f"[ERROR] 目录不存在: {root_path}", [])

    lines = []
    all_files = []

    def _walk(dir_path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(
                dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except PermissionError:
            return

        for entry in entries:
            if entry.name in _SKIP_NAMES:
                continue
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                _walk(entry, prefix + "  ", depth + 1)
            else:
                size = entry.stat().st_size
                if size < 1024:
                    s = f"{size}B"
                elif size < 1024 * 1024:
                    s = f"{size / 1024:.0f}KB"
                else:
                    s = f"{size / 1024 / 1024:.1f}MB"
                lines.append(f"{prefix}{entry.name} ({s})")
                rel = str(entry.relative_to(root)).replace("\\", "/")
                all_files.append(rel)

    lines.append(f"{root.name}/")
    _walk(root, "  ", 0)
    return ("\n".join(lines), all_files)


async def _prescan(
    llm, root_path: str, topic: str,
    max_depth: int = MAX_TREE_DEPTH, max_files: int = MAX_TREE_FILES,
) -> list:
    """
    预扫描阶段：递归扫描完整文件树 → 分批 LLM 筛选候选文件。
    
    带缓存：目录内容不变 + 同一查询 → 跳过 LLM 调用。

    每批最多 max_files 个文件，每批独立调用 LLM 筛选，最后合并去重。
    """
    # 检查缓存（目录哈希 + 查询主题）
    dir_hash = _get_dir_hash(root_path, max_depth)
    cached = _load_prescan_cache(dir_hash, topic)
    if cached is not None:
        logger.debug("预扫描缓存命中 (hash=%s, topic=%s)，跳过 LLM 调用", dir_hash, topic)
        return cached
    
    file_tree, all_files = _build_file_tree(root_path, max_depth=max_depth)
    logger.debug("预扫描文件树:\n%s", file_tree)
    logger.debug("总文件数: %d", len(all_files))

    # 如果文件数 ≤2，无需 LLM 筛选
    if len(all_files) <= 2:
        logger.debug("文件数 ≤2，跳过预筛选")
        return all_files

    root = Path(root_path)

    def _validate(candidates: list) -> list:
        """验证候选路径存在性，尝试多种拼接方式。"""
        valid = []
        for c in candidates:
            p1 = root / c
            p2 = Path(c)
            if p1.exists() or p2.exists():
                if p2.exists() and not p1.exists():
                    try:
                        c = str(p2.relative_to(root)).replace("\\", "/")
                    except ValueError:
                        pass
                valid.append(c)
        return valid

    # 如果文件数 ≤ max_files，单批处理
    if len(all_files) <= max_files:
        candidates = await _prescan_batch(llm, file_tree, topic)
        logger.debug("预筛选候选文件(单批): %s", candidates)
        valid = _validate(candidates)
        logger.debug(
            "验证后有效候选: %s (原 %d → 有效 %d)", valid, len(candidates), len(valid)
        )
        # 保存缓存
        _save_prescan_cache(dir_hash, topic, valid)
        return valid

    # 文件数 > max_files，分批并行处理
    logger.debug("文件数 %d 超过批次大小 %d，分批并行处理", len(all_files), max_files)

    batch_starts = list(range(0, len(all_files), max_files))
    total_batches = (len(all_files) + max_files - 1) // max_files

    # 先构建所有批次的文件树（轻量操作，无需并行）
    batch_configs = []
    for batch_idx in batch_starts:
        batch_files = all_files[batch_idx : batch_idx + max_files]
        batch_tree = _build_batch_tree(root_path, batch_files)
        batch_num = batch_idx // max_files + 1
        batch_configs.append((batch_tree, batch_num))
        logger.debug(
            "批次 %d/%d（%d 个文件）", batch_num, total_batches, len(batch_files)
        )

    # 并行执行所有批次的 LLM 筛选
    batch_results = await asyncio.gather(*[
        _prescan_batch(llm, tree, topic) for tree, _ in batch_configs
    ])

    all_candidates = []
    for candidates in batch_results:
        for c in candidates:
            if c not in all_candidates:
                all_candidates.append(c)

    logger.debug("预筛选候选文件(合并): %s", all_candidates)
    valid = _validate(all_candidates)
    logger.debug(
        "验证后有效候选: %s (原 %d → 有效 %d)", valid, len(all_candidates), len(valid)
    )
    
    # 保存缓存
    _save_prescan_cache(dir_hash, topic, valid)
    return valid


async def _prescan_batch(llm, file_tree: str, topic: str,
                         max_context_tokens: int = MAX_CONTEXT_TOKENS) -> list:
    """单批预扫描：发送文件树给 LLM 筛选候选文件。"""
    prompt = PRESCAN_PROMPT.format(file_tree=file_tree, research_topic=topic)

    # token 保护：截断过长的文件树，预留 system + output 空间
    prompt_tokens = estimate_tokens(prompt)
    if prompt_tokens > max_context_tokens - 200:
        safe_tree = truncate_to_tokens(file_tree, max_context_tokens - 500)
        prompt = PRESCAN_PROMPT.format(file_tree=safe_tree, research_topic=topic)

    messages = [
        SystemMessage(content="你是文件系统预筛选助手，只输出 JSON 数组。"),
        HumanMessage(content=prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw = response.content.strip()
        logger.debug("预筛选 LLM 输出: %s", raw)

        # 清理 markdown 代码块包裹（```json ... ```）
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()

        # 提取 JSON 数组
        json_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            # 修复 LLM 常见的非法转义
            json_str = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
            candidates = json.loads(json_str)
            if isinstance(candidates, list):
                normalized = []
                for c in candidates:
                    if isinstance(c, str):
                        normalized.append(c.replace("\\", "/").lstrip("/"))
                return normalized
    except Exception as e:
        logger.warning("预扫描 LLM 调用失败（跳过本批筛选）: %s", e)

    return []


def _build_batch_tree(root_path: str, batch_files: list) -> str:
    """根据文件路径列表构建精简的文件树字符串（保留目录层级）。"""
    root = Path(root_path)
    dir_files: dict[str, list[str]] = {}
    for f in batch_files:
        parts = f.replace("\\", "/").split("/")
        if len(parts) > 1:
            dir_path = "/".join(parts[:-1])
        else:
            dir_path = ""
        dir_files.setdefault(dir_path, []).append(parts[-1])

    lines = [f"{root.name}/"]
    for dir_path in sorted(dir_files.keys()):
        if dir_path:
            lines.append(f"  {dir_path}/")
            indent = "    "
        else:
            indent = "  "
        for fname in sorted(dir_files[dir_path]):
            lines.append(f"{indent}{fname}")
    return "\n".join(lines)


# ============================================================
# LangGraph 工作流
# ============================================================


def create_agent(api_key: str, model_name: str, base_url: str, 
                 save_llm_log: bool = False, memory: bool = False,
                 verbose: bool = False):
    """
    创建 ReAct Agent 的 LangGraph 工作流。

    START → prescan → agent → (ACTION?) → tools → agent
                        → (ANSWER?) → END
    
    Args:
        memory: 是否启用 MemorySaver 持久化状态（交互模式用）
        verbose: 是否显示工具执行进度
    """
    tools = create_tools(api_key, base_url, model_name, verbose=verbose)

    # LLM 对话历史日志（JSONL）
    llm_messages = None
    llm_log_path = None
    if save_llm_log:
        from datetime import datetime
        sessions_dir = Path("sessions")
        sessions_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        llm_log_path = sessions_dir / f"llm_{ts}.jsonl"
        llm_messages = []

    llm = ChatOpenAI(
        model=model_name,
        temperature=0.0,
        max_retries=2,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )

    prescan_llm = ChatOpenAI(
        model=model_name,
        temperature=0.0,
        max_retries=1,
        openai_api_key=api_key,
        openai_api_base=base_url,
    )

    async def prescan_node(state: ExplorerState, config: Optional[RunnableConfig] = None) -> dict:
        """预扫描阶段：扫描文件树，LLM 筛选候选文件。"""
        if config and config.get("configurable", {}).get("no_prescan"):
            logger.debug("预扫描已跳过（--no-prescan）")
            return {"file_candidates": []}

        topic = state.get("research_topic", "")
        root_path = state.get("root_path", DEFAULT_ROOT_PATH)
        max_depth = state.get("max_tree_depth", MAX_TREE_DEPTH)
        max_files = state.get("max_tree_files", MAX_TREE_FILES)
        logger.debug("===== 预扫描开始 =====")
        candidates = await _prescan(
            prescan_llm, root_path, topic,
            max_depth=max_depth, max_files=max_files,
        )
        logger.debug("预扫描完成，候选文件数: %d", len(candidates))
        return {"file_candidates": candidates}

    async def agent_node(state: ExplorerState) -> dict:
        """调用 LLM 生成下一轮思考/行动。"""
        conversation = state.get("conversation", "")
        topic = state.get("research_topic", "")
        root_path = state.get("root_path", DEFAULT_ROOT_PATH)
        max_iter = state.get("max_iterations", MAX_EXPLORATION_ITERATIONS)
        count = state.get("iteration_count", 0)
        max_tokens = state.get("maxTokens", MAX_CONTEXT_TOKENS)

        remaining = max_iter - count

        if remaining <= 0:
            new_conv = (
                conversation
                + f"\n\n助手:\nTHOUGHT: 已达到探索次数上限 ({max_iter})，必须给出最终答案。\n"
                + "ANSWER: 已达到最大探索次数限制，信息不足，请尝试更精确的查询。"
            )
            return {"conversation": new_conv, "iteration_count": count + 1}

        system_message = AGENT_SYSTEM_PROMPT.format(
            max_iterations=max_iter,
            research_topic=topic,
            root_path=root_path,
            max_steps=state.get("max_steps", MAX_STEPS),
            file_candidates="\n".join(
                f"  - {c}" for c in state.get("file_candidates", [])
            ) or "  （无候选文件，请自行探索）",
        )

        # 提前警告：剩余轮次不足时强制收网
        WARNING_THRESHOLD = 3
        if remaining <= WARNING_THRESHOLD:
            system_message += (
                f"\n\n[系统警告] 仅剩 {remaining} 轮探索机会。"
                "请立即基于已有信息给出最完整的答案，不要再发起新的工具调用。"
                "如果信息确实不足，也要尽力给出能回答的部分。"
            )

        messages = [SystemMessage(content=system_message)]

        # 截断 conversation，避免上下文无限增长
        max_context = state.get("max_context_rounds", MAX_CONTEXT_ROUNDS)
        truncated_conv = truncate_conversation(conversation, max_context)

        if not truncated_conv.strip():
            messages.append(HumanMessage(content=topic))
        else:
            # 将 conversation 字符串转为交替消息
            chunks = truncated_conv.split("助手:\n")
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue

                if "---OBSERVATION---" in chunk:
                    obs_idx = chunk.index("---OBSERVATION---")
                    assistant_part = chunk[:obs_idx].strip()
                    obs_content = chunk[obs_idx + 15 :].strip()

                    if assistant_part:
                        messages.append(AIMessage(content=f"助手:\n{assistant_part}"))
                    messages.append(
                        HumanMessage(content=f"观察结果:\n{obs_content}")
                    )
                else:
                    messages.append(AIMessage(content=f"助手:\n{chunk}"))
                    if has_answer(chunk):
                        messages.append(
                            HumanMessage(
                                content=(
                                    "你刚才给出了一个初步答案。请评估：\n"
                                    "1. 如果信息已经充分，输出更完整的最终答案"
                                    "（仍然以 ANSWER: 开头）\n"
                                    "2. 如果发现遗漏或需要补充，使用工具继续搜索，"
                                    "然后给出更全面的答案"
                                )
                            )
                        )
                    else:
                        messages.append(
                            HumanMessage(
                                content="请继续分析。如果需要使用工具，输出 ACTION；"
                                "如果已有足够信息，输出 ANSWER。"
                            )
                        )

        debug_messages(messages)

        # token 保护：估算总 token 数，超出时从最早的消息开始截断
        total_tokens = estimate_tokens(system_message) + sum(
            estimate_tokens(m.content) for m in messages[1:]
        )
        if total_tokens > max_tokens - 200:
            # 保留 system prompt，从最早的消息开始截断
            budget = max_tokens - estimate_tokens(system_message) - 200
            kept = []
            for m in reversed(messages[1:]):
                t = estimate_tokens(m.content)
                if budget - t < 0:
                    break
                budget -= t
                kept.append(m)
            kept.reverse()
            messages = [messages[0]] + kept
            logger.debug("token 截断: %d → %d 条消息", len(messages), len(messages))

        try:
            response = await llm.ainvoke(messages)
            response_text = response.content.strip()
            logger.debug(
                "---------- LLM 输出 ----------\n%s\n-------------------------------",
                response_text,
            )
        except Exception as e:
            logger.error("LLM API 调用失败: %s", e)
            response_text = (
                f"THOUGHT: API 调用出错。\nANSWER: API 错误: {str(e)}"
            )

        # JSON 对话日志 — 记录助手回复（去重 + 去内部前缀）
        if llm_messages is not None:
            clean = response_text
            if clean.startswith("助手:\n"):
                clean = clean[len("助手:\n"):]
            # 连续相同内容不重复记录
            if not llm_messages or llm_messages[-1].get("content") != clean:
                llm_messages.append({"role": "assistant", "content": clean})

        new_conv = conversation
        if new_conv:
            new_conv += "\n\n"
        new_conv += f"助手:\n{response_text}"

        return {"conversation": new_conv, "iteration_count": count + 1}

    async def tools_node(state: ExplorerState) -> dict:
        """执行工具并追加观察结果。"""
        conversation = state.get("conversation", "")

        # 取最后一条助手消息
        parts = conversation.split("助手:\n")
        last_assistant = parts[-1] if len(parts) > 1 else ""

        parsed = parse_action(last_assistant)

        if parsed is None:
            # 容错：尝试从文本中提取路径
            logger.debug("工具解析失败，尝试容错提取路径")
            raw_path = parse_path_from_text(last_assistant)
            obs = None
            if raw_path:
                corrected, note = resolve_path(raw_path)
                if corrected.exists() and corrected.is_dir():
                    try:
                        result_text = tools["list_directory"](str(corrected))
                        if note:
                            result_text = f"[自动纠正: {note}]\n{result_text}"
                        obs = f"---OBSERVATION---\n{result_text}"
                    except Exception:
                        pass
            if obs is None:
                # 提供详细的格式说明和可用工具列表
                available_tools = ", ".join(tools.keys())
                obs = (
                    '---OBSERVATION---\n'
                    '[ERROR] 无法解析工具调用。\n\n'
                    '【正确格式】\n'
                    'ACTION: tool_name(arg1="val1", arg2="val2")\n\n'
                    f'【可用工具】\n{available_tools}\n\n'
                    '【示例】\n'
                    'ACTION: list_directory(path="SeekerDocs")\n'
                    'ACTION: read_file(path="SeekerDocs/report.txt")\n'
                    'ACTION: search_files(pattern="*.pdf", root_path="SeekerDocs")\n'
                    'ACTION: grep_content(keyword="关键词", root_path="SeekerDocs")\n'
                    'ACTION: extract_content(file_path="SeekerDocs/report.txt", topic="查询主题")\n'
                    'ACTION: batch_extract(dir_path="SeekerDocs", topic="查询主题")'
                )
        else:
            tool_name, args = parsed
            tool_func = tools.get(tool_name)
            logger.debug("工具调用: %s(%s)", tool_name, args)

            # 从 state 注入动态参数
            if tool_name == "read_file":
                if "max_lines" not in args:
                    args["max_lines"] = state.get("max_file_lines", MAX_FILE_LINES)
                if "max_file_size_mb" not in args:
                    args["max_file_size_mb"] = state.get(
                        "max_file_size_mb", MAX_FILE_SIZE_MB
                    )

            # extract_content: topic 默认用用户原始查询
            if tool_name == "extract_content" and "topic" not in args:
                args["topic"] = state.get("research_topic", "")

            if tool_func is None:
                obs = (
                    f"---OBSERVATION---\n"
                    f"[ERROR] 未知工具: {tool_name}\n"
                    f"可用: {', '.join(tools.keys())}"
                )
            else:
                try:
                    if asyncio.iscoroutinefunction(tool_func):
                        result_text = await tool_func(**args)
                    else:
                        result_text = tool_func(**args)
                    logger.debug(
                        "工具结果 (%s): %s", tool_name, result_text[:500]
                    )
                    obs = f"---OBSERVATION---\n{result_text}"
                except TypeError as e:
                    # 参数错误 → 提供详细信息帮助 LLM 修正
                    sig = inspect.signature(tool_func)
                    params = list(sig.parameters.keys())
                    obs = (
                        f"---OBSERVATION---\n"
                        f"[ERROR] {tool_name} 参数错误: {e}\n\n"
                        f"【工具签名】{tool_name}({', '.join(params)})\n"
                        f"【你传的参数】{args}\n\n"
                        f"请检查参数名是否正确，然后重试。"
                    )
                except Exception as e:
                    obs = (
                        f"---OBSERVATION---\n"
                        f"[ERROR] {tool_name} 执行出错: {e}"
                    )

        # JSON 对话日志 — 记录工具结果
        if llm_messages is not None:
            tool_content = obs.replace("---OBSERVATION---\n", "", 1)
            llm_messages.append({"role": "tool", "content": tool_content})

        return {"conversation": conversation + "\n\n" + obs}

    def should_continue(state: ExplorerState) -> str:
        """
        条件边：决定走 tools、agent 还是 end。

        ANSWER 后的路由策略：
        - 若仍有剩余迭代次数 → 回到 agent，让 LLM 自行判断是否需要补充搜索
        - 若已达迭代上限 → END
        """
        parts = state.get("conversation", "").split("助手:\n")
        if len(parts) < 2:
            logger.debug("路由决策: end (无助手消息)")
            return "end"

        last = parts[-1]
        if parse_action(last) is not None:
            logger.debug("路由决策: tools (检测到 ACTION)")
            return "tools"
        if has_answer(last):
            count = state.get("iteration_count", 0)
            max_iter = state.get("max_iterations", MAX_EXPLORATION_ITERATIONS)
            if count < max_iter:
                logger.debug(
                    "路由决策: agent (ANSWER 后仍有 %d 轮剩余)", max_iter - count
                )
                return "agent"
            logger.debug("路由决策: end (ANSWER 且已达迭代上限)")
            return "end"
        if "---OBSERVATION---" in last:
            logger.debug("路由决策: agent (检测到 OBSERVATION)")
            return "agent"
        logger.debug("路由决策: end (默认)")
        return "end"

    # 构建图
    builder = StateGraph(ExplorerState)
    builder.add_node("prescan", prescan_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "prescan")
    builder.add_edge("prescan", "agent")
    builder.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "agent": "agent", "end": END},
    )
    builder.add_edge("tools", "agent")

    # 可选：使用 MemorySaver 持久化状态（交互模式用）
    checkpointer = None
    if memory:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer), llm_messages, llm_log_path


# ============================================================
# 便捷函数
# ============================================================


def _build_initial_state(user_query: str, **overrides) -> ExplorerState:
    """构建 Agent 初始状态，用 config 字段覆盖默认值。"""
    cfg = overrides.pop("_cfg", None)
    defaults = {
        "conversation": "",
        "iteration_count": 0,
        "research_topic": user_query,
        "root_path": cfg.root_path if cfg else DEFAULT_ROOT_PATH,
        "file_candidates": [],
        "max_iterations": cfg.max_iterations if cfg else MAX_EXPLORATION_ITERATIONS,
        "max_file_lines": cfg.max_file_lines if cfg else MAX_FILE_LINES,
        "max_tree_depth": cfg.max_tree_depth if cfg else MAX_TREE_DEPTH,
        "max_tree_files": cfg.max_tree_files if cfg else MAX_TREE_FILES,
        "max_steps": cfg.max_steps if cfg else MAX_STEPS,
        "max_chunk_lines": cfg.max_chunk_lines if cfg else MAX_CHUNK_LINES,
        "max_snippet_length": cfg.max_snippet_length if cfg else MAX_SNIPPET_LENGTH,
        "max_context_rounds": cfg.max_context_rounds if cfg else MAX_CONTEXT_ROUNDS,
        "max_tool_output": cfg.max_tool_output if cfg else 8000,
        "max_file_size_mb": cfg.max_file_size_mb if cfg else MAX_FILE_SIZE_MB,
        "maxTokens": cfg.maxTokens if cfg else MAX_CONTEXT_TOKENS,
    }
    defaults.update(overrides)
    return defaults


async def run_agent(
    user_query: str,
    api_key: str = "",
    model_name: str = "",
    base_url: str = "",
    config=None,
    save_llm_log: bool = False,
) -> str:
    """
    一句自然语言启动探索，返回最终答案。

    支持两种调用方式：
    1. 传统方式：传入各个单独参数
    2. Config 方式：传入 AgentConfig 对象（推荐）
    """
    # 从 config 提取参数（如果提供）
    if config is not None:
        api_key = config.api_key
        model_name = config.model_name
        base_url = config.base_url
        save_llm_log = config.save_llm_log

    graph, llm_messages, llm_log_path = create_agent(
        api_key, model_name, base_url, save_llm_log=save_llm_log,
        verbose=getattr(config, 'verbose', False) if config else False,
    )

    # JSON 对话日志 — 记录用户查询
    if llm_messages is not None:
        llm_messages.append({"role": "user", "content": user_query})

    initial_state = _build_initial_state(user_query, _cfg=config)

    result = await graph.ainvoke(
        initial_state, {"configurable": {"thread_id": "DocSeeker"}}
    )

    conversation = result.get("conversation", "")

    # JSONL 对话日志 — 写入文件
    if llm_messages is not None and llm_log_path is not None:
        with open(llm_log_path, "w", encoding="utf-8") as f:
            for msg in llm_messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # 普通文本日志 — 写入 logs/ 目录
    if save_llm_log and conversation.strip():
        from datetime import datetime
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"run_{ts}.txt"
        log_path.write_text(conversation, encoding="utf-8")

    m = re.search(r"ANSWER:\s*(.*?)(?:\n\n|\Z)", conversation, re.DOTALL)
    if m:
        return m.group(1).strip()

    parts = conversation.split("助手:\n")
    if len(parts) > 1:
        return parts[-1].strip()

    return conversation.strip()
