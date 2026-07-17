"""
DocSeeker CLI

用法:
  python cli.py "在 .QADocs 中查找关于贷款客户群分析的内容"
  python cli.py "查找 PDF 文件" --root .QADocs
  python cli.py --interactive
  python cli.py "查询内容" --verbose
  python cli.py "查询内容" --debug
"""

import asyncio
import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

_root_dir = os.path.dirname(os.path.abspath(__file__))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from src.graph import create_agent, _build_initial_state
from src.config import build_config, AgentConfig

logger = logging.getLogger(__name__)


# ============================================================
# 参数解析
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        prog="DocSeeker",
        description="文件系统自主探索智能体 — 自然语言描述需求，AI 自主探索文件系统找到答案",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py "在 .QADocs 中查找关于贷款风险的分析"
  python cli.py "找关于客户流失的文档" --root .QADocs --verbose
  python cli.py --interactive
  python cli.py "哪些文件提到了财务报表" -r . -i 15
        """,
    )
    parser.add_argument("query", nargs="?", default=None, help="自然语言查询")
    parser.add_argument("-r", "--root", default=None, help="起始目录")
    parser.add_argument("-k", "--api-key", default=None, help="API Key")
    parser.add_argument("-m", "--model", default=None, help="模型名称")
    parser.add_argument("-u", "--base-url", default=None, help="API 地址")
    parser.add_argument("-i", "--max-iterations", type=int, default=None, help="最大轮数")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    parser.add_argument("--verbose", action="store_true", help="显示详细过程")
    parser.add_argument("--debug", action="store_true", help="调试日志（打印 LLM 完整对话）")
    parser.add_argument("--no-prescan", action="store_true", help="跳过预扫描（省一次 LLM 调用）")
    parser.add_argument("--save-llm-log", action="store_true", help="将 LLM 完整对话保存到本地日志文件")
    parser.add_argument("--mcp", action="store_true", help="启动 MCP 服务（供 Claude Desktop 等 Agent 调用）")
    parser.add_argument("--mcp-transport", default="stdio", choices=["stdio", "sse"], help="MCP 传输方式（默认 stdio）")
    parser.add_argument("--max-file-lines", type=int, default=None, help="read_file 单文件最大行数")
    parser.add_argument("--max-tree-depth", type=int, default=None, help="预扫描文件树最大深度")
    parser.add_argument("--max-tree-files", type=int, default=None, help="预扫描文件树最大文件数")
    parser.add_argument("--max-steps", type=int, default=None, help="Agent 典型完成步骤数")
    parser.add_argument("--max-chunk-lines", type=int, default=None, help="extract_content 大文件分块行数")
    parser.add_argument("--max-snippet-length", type=int, default=None, help="extract_content 每段 snippet 最大字符数")
    parser.add_argument("--max-context-rounds", type=int, default=None, help="保留最近 N 轮对话")
    parser.add_argument("--max-tool-output", type=int, default=None, help="单个工具输出最大字符数")
    parser.add_argument("--maxTokens", type=int, default=None, help="LLM 上下文 token 上限")
    parser.add_argument("--max-file-size", type=int, default=None, help="文件读取大小上限 MB")
    return parser.parse_args()


# ============================================================
# 辅助
# ============================================================

def truncate(text: str, max_len: int = 200) -> str:
    """截断文本"""
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def _diff_conversation(before: str, after: str):
    """解析 conversation 前后差异"""
    if before == after or len(after) <= len(before):
        return None, False, False, False

    added = after[len(before):].strip()
    if not added:
        return None, False, False, False

    return added, "ACTION:" in added, "---OBSERVATION---" in added, "ANSWER:" in added


def _record_query_history(cfg: AgentConfig, query: str, answer: str,
                          tool_count: int, start_time: float):
    """将查询记录追加到 sessions/history.jsonl。"""
    sessions_dir = Path("sessions")
    sessions_dir.mkdir(exist_ok=True)
    history_path = sessions_dir / "history.jsonl"

    record = {
        "timestamp": datetime.now().isoformat(),
        "query": query,
        "answer": (answer[:500] + "...") if answer and len(answer) > 500 else answer,
        "root_path": cfg.root_path,
        "model": cfg.model_name,
        "tool_count": tool_count,
        "duration_s": round(time.time() - start_time, 1),
    }
    try:
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("写入查询历史失败: %s", e)


# ============================================================
# 探索执行
# ============================================================

async def run_query(cfg: AgentConfig, user_query: str, _graph=None, _thread_id: str = None) -> str:
    """执行一次探索并返回最终答案。"""
    _start_time = time.time()

    # 如果提供了预构建的 graph，直接使用；否则创建新的
    if _graph is not None:
        graph = _graph
        llm_messages = None
        llm_log_path = None
    else:
        graph, llm_messages, llm_log_path = create_agent(cfg.api_key, cfg.model_name, cfg.base_url, save_llm_log=cfg.save_llm_log, verbose=cfg.verbose)

    # JSON 对话日志 — 记录用户查询
    if llm_messages is not None:
        llm_messages.append({"role": "user", "content": user_query})

    # 使用提供的 thread_id（交互模式），或默认值（单次查询）
    thread_id = _thread_id or "DocSeeker"
    
    # 构建初始状态
    initial_state = _build_initial_state(user_query, _cfg=cfg)
    
    config = {"configurable": {"thread_id": thread_id, "no_prescan": cfg.no_prescan}}

    if cfg.verbose:
        print(f"\n  [目标] {user_query}")
        print(f"  [目录] {cfg.root_path}")
        print(f"  [轮数] {cfg.max_iterations}\n")

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("===== 开始探索 =====\n查询: %s\n根目录: %s", user_query, cfg.root_path)

    final_answer = ""
    tool_count = 0
    prev_conv = ""
    prev_tool_conv = ""
    iteration = 0
    conversation = ""

    async for event in graph.astream(initial_state, config):
        for node_name, output in event.items():
            conv = output.get("conversation", "")
            conversation = conv  # 保存最新的 conversation

            if node_name == "prescan":
                candidates = output.get("file_candidates", [])
                if cfg.verbose and candidates:
                    print(f"\n  [预扫描] 筛选出 {len(candidates)} 个候选文件:")
                    for c in candidates:
                        print(f"    - {c}")
                elif cfg.verbose:
                    print("\n  [预扫描] 未找到明显相关文件，Agent 将自主探索")

            elif node_name == "agent":
                iteration += 1
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("------ 迭代 %d: agent 节点 ------", iteration)

                added, is_action, _, is_answer = _diff_conversation(
                    prev_conv or prev_tool_conv, conv
                )
                prev_conv = conv

                if added:
                    if is_answer:
                        m = re.search(r"ANSWER:\s*(.*)", added, re.DOTALL)
                        if m:
                            final_answer = m.group(1).strip()
                    elif is_action:
                        tool_count += 1
                        m = re.search(r"ACTION:\s*(\w+)\s*\(([^)]*)\)", added)
                        if m and cfg.verbose:
                            name, args_str = m.group(1), m.group(2)
                            print(f"\n  [工具 #{tool_count}] {name}({args_str})")
                    elif cfg.verbose:
                        thought = added
                        for prefix in ["THOUGHT:", "ACTION:", "ANSWER:"]:
                            idx = thought.find(prefix)
                            if idx >= 0:
                                thought = thought[:idx].strip() if idx > 0 else ""
                                break
                        if thought and thought.strip() not in ("助手:", ""):
                            print(f"\n  [思考] {truncate(thought, 300)}")

            elif node_name == "tools":
                added, _, _, _ = _diff_conversation(prev_conv, conv)
                prev_conv = conv
                prev_tool_conv = conv
                if added and cfg.verbose:
                    obs = added.replace("---OBSERVATION---", "").strip()
                    if obs and not obs.startswith("[ERROR]"):
                        print(f"  [结果] {truncate(obs, 400)}")
                    elif obs:
                        print(f"  [!] {obs}")

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("===== 探索结束 =====\n最终答案: %s", final_answer or "(无答案)")

    # JSONL 对话日志 — 写入文件
    if llm_messages is not None and llm_log_path is not None:
        with open(llm_log_path, "w", encoding="utf-8") as f:
            for msg in llm_messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    # 普通文本日志 — 写入 logs/ 目录
    if cfg.save_llm_log and conversation.strip():
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"run_{ts}.txt"
        log_path.write_text(conversation, encoding="utf-8")

    # 查询历史 — 追加到 sessions/history.jsonl
    _record_query_history(cfg, user_query, final_answer, tool_count, _start_time)

    return final_answer


# ============================================================
# 交互模式
# ============================================================

async def interactive_loop(cfg: AgentConfig):
    """交互式问答循环（支持上下文保持）"""
    # 创建带 MemorySaver 的 graph，保持会话状态
    session_id = str(uuid.uuid4())[:8]  # 每个交互会话唯一 ID
    
    graph, llm_messages, llm_log_path = create_agent(
        cfg.api_key, cfg.model_name, cfg.base_url, 
        save_llm_log=cfg.save_llm_log, memory=True, verbose=cfg.verbose
    )
    
    print()
    print("=" * 60)
    print("  DocSeeker — 文件系统自主探索智能体")
    print("  输入自然语言描述需求，AI 自主找到答案")
    print("  输入 'exit' 或 'q' 退出")
    print(f"  根目录: {cfg.root_path}")
    print(f"  模型: {cfg.model_name}")
    print(f"  会话 ID: {session_id}（上下文已保持）")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n[查询] > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("再见！")
                break

            answer = await run_query(cfg, user_input, _graph=graph, _thread_id=session_id)

            print(f"\n{'=' * 60}")
            print(f"[回答]\n{answer or '(无答案)'}")
            print(f"{'=' * 60}")

        except KeyboardInterrupt:
            print("\n退出...")
            break


# ============================================================
# Main
# ============================================================

def main():
    args = parse_args()

    # 构建统一配置
    cfg = build_config(args)

    # 日志
    log_level = logging.DEBUG if cfg.debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    if not cfg.api_key:
        print("[错误] 未提供 API Key。请设置 OPENAI_API_KEY 环境变量或使用 --api-key。")
        sys.exit(1)

    # MCP 服务模式
    if cfg.mcp:
        from src.mcp_server import init_mcp_server, run_mcp_server
        init_mcp_server(
            root_path=cfg.root_path,
            api_key=cfg.api_key,
            model_name=cfg.model_name,
            base_url=cfg.base_url,
        )
        print(f"[MCP] DocSeeker MCP 服务启动中 (transport={cfg.mcp_transport})")
        print(f"[MCP] 根目录: {cfg.root_path}")
        print(f"[MCP] 模型: {cfg.model_name}")
        run_mcp_server(transport=cfg.mcp_transport)
        return

    if cfg.interactive or not args.query:
        asyncio.run(interactive_loop(cfg))
    else:
        answer = asyncio.run(run_query(cfg, args.query))
        print(f"\n{'=' * 60}\n[回答]\n{answer or '(无答案)'}\n{'=' * 60}")


if __name__ == "__main__":
    main()
