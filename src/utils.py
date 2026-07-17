"""
通用工具函数 — 路径解析、文本提取、对话截断、Token 估算

这些函数不依赖 LangGraph 或 LLM，是纯工具函数。
"""

import difflib
import logging
import re
from pathlib import Path

from .config import MAX_CONTEXT_ROUNDS, MAX_CONTEXT_TOKENS

logger = logging.getLogger(__name__)


# ============================================================
# 调试辅助
# ============================================================

def debug_messages(messages: list):
    """打印发送给 LLM 的完整消息列表（DEBUG 级别）。"""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug("========== LLM 输入 (%d 条消息) ==========", len(messages))
    for i, msg in enumerate(messages):
        role = type(msg).__name__.replace("Message", "")
        content = msg.content if hasattr(msg, "content") else str(msg)
        if len(content) > 800:
            content = content[:800] + f"\n... (共 {len(content)} 字符，已截断)"
        logger.debug("  [%d] %s: %s", i, role, content)
    logger.debug("=" * 50)


# ============================================================
# Token 估算与截断
# ============================================================

def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量（不依赖 tiktoken）。

    策略：中文字符约 1 token/字，英文约 1 token/4 字符，
    加上每条消息约 4 token 的格式开销。
    """
    if not text:
        return 0
    cn = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cn
    return cn + (other + 3) // 4


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """
    按 token 估算截断文本，保留前 max_tokens 个 token 的内容。
    超限时在末尾追加截断标记。
    """
    if not text or max_tokens <= 0:
        return text
    if estimate_tokens(text) <= max_tokens:
        return text
    # 逐字符扫描，达到上限时截断
    cn_count = 0
    en_count = 0
    for i, ch in enumerate(text):
        if '\u4e00' <= ch <= '\u9fff':
            cn_count += 1
        else:
            en_count += 1
        if cn_count + (en_count + 3) // 4 >= max_tokens:
            return text[:i] + f"\n\n... (已截断至约 {max_tokens} token)"
    return text


# ============================================================
# 对话上下文截断
# ============================================================

def truncate_conversation(
    conversation: str,
    max_rounds: int = MAX_CONTEXT_ROUNDS,
) -> str:
    """
    截断 conversation，保留最近 N 轮完整对话。

    conversation 格式: "助手:\\n... \\n\\n---OBSERVATION---\\n...\\n\\n助手:\\n..."
    每轮 = 一条助手消息 + 观察结果（可选）

    策略：
    1. 按 "助手:\\n" 分割为 chunks
    2. 每个 chunk 如果包含 "---OBSERVATION---" 则是一轮完整对话
    3. 保留最后 max_rounds 轮，丢弃早期轮次
    4. 对单个观察结果超长的也做截断
    """
    if not conversation.strip():
        return conversation

    chunks = conversation.split("助手:\n")
    if len(chunks) <= 1:
        return conversation

    # 重建为 (assistant_text, observation_text) 对
    rounds = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if "---OBSERVATION---" in chunk:
            obs_idx = chunk.index("---OBSERVATION---")
            assistant_part = chunk[:obs_idx].strip()
            obs_content = chunk[obs_idx + 15:].strip()
            rounds.append((assistant_part, obs_content))
        else:
            rounds.append((chunk, None))

    # 保留最近 max_rounds 轮
    if len(rounds) > max_rounds:
        dropped = len(rounds) - max_rounds
        logger.debug(
            "conversation 截断: %d 轮 → %d 轮（丢弃 %d 轮早期对话）",
            len(rounds), max_rounds, dropped,
        )
        rounds = rounds[-max_rounds:]

    # 重建 conversation
    parts = []
    for assistant_part, obs_content in rounds:
        parts.append(f"助手:\n{assistant_part}")
        if obs_content is not None:
            parts.append(f"---OBSERVATION---\n{obs_content}")

    return "\n\n".join(parts)


# ============================================================
# 路径解析
# ============================================================

def resolve_path(path_str: str, root_path: str = None) -> tuple[Path, str]:
    """
    路径自动纠正。两级策略:
      1. 父目录名模糊匹配（如 .QQQocs → .QADocs）
      2. 文件名模糊匹配（递归搜索最相似文件）

    Args:
        path_str: 要解析的路径
        root_path: 可选根目录，相对路径会基于此解析

    返回: (corrected_path, note)
    """
    p = Path(path_str)
    if not p.is_absolute() and root_path:
        p = Path(root_path) / p
    if p.exists():
        return p, ""

    # 策略1: 父目录名模糊匹配
    parent = p.parent
    if parent.exists():
        try:
            candidates = [item.name for item in parent.iterdir()]
            close = difflib.get_close_matches(p.name, candidates, n=1, cutoff=0.4)
            if close:
                corrected = parent / close[0]
                if corrected.exists():
                    return corrected, f"自动纠正: {path_str} -> {corrected}"
        except PermissionError:
            pass

    # 策略2: 文件名模糊匹配（在父目录子树中搜索）
    threshold = 0.4
    best, best_score = p, 0.0
    try:
        search_root = p.parent if p.parent.exists() else Path(".")
        for candidate in search_root.rglob("*"):
            if not candidate.is_file():
                continue
            filename = candidate.name
            score = difflib.SequenceMatcher(
                None, p.name.lower(), filename.lower()
            ).ratio()
            if score > best_score and score > threshold:
                best_score = score
                best = candidate
        if best != p:
            return best, f"自动定位: {path_str} -> {best}"
    except Exception:
        pass

    return p, ""


def parse_path_from_text(text: str) -> str:
    """
    从模型输出中提取文件/目录路径（容错用）。

    当 parse_action 失败时调用，尝试从混乱文本中提取路径。
    """
    # 找 ACTION: xxx(...) 中括号内容
    action_match = re.search(r"ACTION:\s*\w+\s*\((.+)\)", text, re.DOTALL)
    content = action_match.group(1) if action_match else text

    # 提取引号中的值
    for q in ['"', "'"]:
        m = re.search(rf'{q}([^"{q}]+){q}', content)
        if m:
            val = m.group(1).strip()
            if val and not val.startswith("="):
                return val

    # 提取等号后面的内容
    m = re.search(r'=\s*(\S+)', content)
    if m:
        return m.group(1).strip().strip('"').strip("'")

    # 提取看起来像路径的字符串
    m = re.search(r'[\w\./\\\-]+\.[\w]+', content)
    if m:
        return m.group(0)

    return ""


# ============================================================
# ACTION / ANSWER 解析
# ============================================================

# 位置参数映射（无参数名时按顺序填充）
POSITIONAL_MAP = {
    "list_directory": ["path"],
    "read_file": ["path"],
    "search_files": ["pattern", "root_path"],
    "extract_content": ["file_path", "topic"],
}


def parse_action(text: str) -> tuple[str, dict] | None:
    """
    从模型输出中解析 ACTION 行。

    格式: ACTION: tool_name(arg1="val1", arg2="val2")
    也支持: ACTION: tool_name("val")（无参数名）
    支持嵌套括号（如参数值中包含括号）

    返回: (tool_name, args_dict) 或 None
    """
    # 先找到 ACTION: xxx( 的起始位置
    action_match = re.search(r"ACTION:\s*(\w+)\s*\(", text, re.MULTILINE)
    if not action_match:
        return None

    tool_name = action_match.group(1)

    # 从左括号开始，用括号计数找到匹配的右括号
    start = action_match.end()
    depth = 1
    i = start
    in_quote = None
    while i < len(text) and depth > 0:
        ch = text[i]
        if in_quote:
            if ch == in_quote and (i == 0 or text[i - 1] != "\\"):
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1

    if depth != 0:
        return None

    args_text = text[start : i - 1].strip()

    args = {}
    if args_text:
        # 解析 key="value" 键值对
        kv_pairs = list(
            re.finditer(
                r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))',
                args_text,
            )
        )
        for kv in kv_pairs:
            key = kv.group(1)
            value = kv.group(2) or kv.group(3) or kv.group(4)
            args[key] = value

        # 无键值对 → 尝试位置参数
        if not kv_pairs:
            q = re.search(r'"([^"]*)"', args_text) or re.search(
                r"'([^']*)'", args_text
            )
            if q:
                args["_pos_1"] = q.group(1)
            else:
                cleaned = args_text.strip().strip('"').strip("'")
                if cleaned:
                    args["_pos_1"] = cleaned

    # 位置参数映射
    pos_keys = [k for k in args if k.startswith("_pos_")]
    if pos_keys and tool_name in POSITIONAL_MAP:
        param_names = POSITIONAL_MAP[tool_name]
        new_args = {}
        for i, pname in enumerate(param_names):
            pos_key = f"_pos_{i + 1}"
            if pos_key in args:
                new_args[pname] = args.pop(pos_key)
            elif pname in args:
                new_args[pname] = args[pname]
        new_args.update(args)
        args = new_args

    return tool_name, args


def has_answer(text: str) -> bool:
    """检查是否包含 ANSWER 标记。"""
    return bool(re.search(r"^ANSWER:\s*", text, re.MULTILINE))
