"""Agent 提示词模板 — 从 prompts/ 目录加载"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """从 prompts/ 目录加载提示词文本文件"""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    return path.read_text(encoding="utf-8")


PRESCAN_PROMPT = _load_prompt("prescan")
EXTRACT_PROMPT = _load_prompt("extract")
AGENT_SYSTEM_PROMPT = _load_prompt("agent_system")
