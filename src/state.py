"""Agent 状态定义"""

from typing import TypedDict, List, Optional


class ExplorerState(TypedDict):
    """
    纯文本 ReAct 循环状态。

    使用字符串 conversation 存储完整对话历史，
    不依赖 LangChain 消息对象的 tool_calls 机制。
    """
    conversation: str
    """累积对话文本（用户消息 + 助手思考 + 工具观察结果）"""
    iteration_count: int
    """当前迭代次数"""
    max_iterations: int
    """最大迭代次数"""
    research_topic: str
    """用户的研究主题/查询"""
    root_path: str
    """探索起始目录"""
    file_candidates: List[str]
    """预筛选的候选文件路径列表（由 prescan 节点填充）"""
    max_file_lines: int
    """read_file 单文件最大读取行数"""
    max_tree_depth: int
    """预扫描文件树最大递归深度"""
    max_tree_files: int
    """预扫描文件树最大文件数"""
    max_steps: int
    """Agent 典型完成步骤数（注入 prompt）"""
    max_chunk_lines: int
    """extract_content 大文件分块行数"""
    max_snippet_length: int
    """extract_content 每段 snippet 最大字符数"""
    max_context_rounds: int
    """conversation 上下文截断：保留最近 N 轮对话"""
    max_tool_output: int
    """单个工具输出最大字符数"""
    max_file_size_mb: int
    """文件读取大小上限（MB）"""
    maxTokens: int
    """LLM 上下文 token 上限"""
