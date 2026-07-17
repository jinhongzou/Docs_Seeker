"""
配置模块 — 从 conf/config.yaml 加载配置，支持默认值回退

加载顺序（优先级从高到低）：
1. CLI 参数（由 argparse 传入）
2. conf/config.yaml 文件
3. 硬编码默认值（AgentConfig dataclass 默认值）

使用方式：
    from src.config import AgentConfig, build_config

    # 方式1：通过 CLI args 构建
    cfg = build_config(args)

    # 方式2：直接创建（测试/库调用）
    cfg = AgentConfig(api_key="xxx", model_name="gpt-4o")
"""

from __future__ import annotations

import os
import sys
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


# ============================================================
# 数据模型
# ============================================================

@dataclass
class AgentConfig:
    """统一的运行时配置。"""

    # --- 连接 ---
    api_key: str = ""
    model_name: str = "Qwen/Qwen2.5-7B-Instruct"
    base_url: str = "https://api.siliconflow.cn/v1"

    # --- 探索 ---
    root_path: str = "."
    max_iterations: int = 10
    max_steps: int = 4
    no_prescan: bool = False

    # --- 文件读取 ---
    max_file_lines: int = 500
    max_file_size_mb: int = 50

    # --- 内容提取 ---
    max_chunk_lines: int = 500
    max_snippet_length: int = 600

    # --- 预扫描 ---
    max_tree_depth: int = 4
    max_tree_files: int = 200

    # --- 上下文管理 ---
    max_context_rounds: int = 6
    max_tool_output: int = 8000
    maxTokens: int = 32768

    # --- 运行模式 ---
    verbose: bool = False
    debug: bool = False
    interactive: bool = False
    save_llm_log: bool = False
    mcp: bool = False
    mcp_transport: str = "stdio"

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# YAML 字段映射
# ============================================================

# conf/config.yaml 键名 → AgentConfig 字段名
YAML_TO_FIELD = {
    "api_key": "api_key",
    "base_url": "base_url",
    "model": "model_name",
    "root_path": "root_path",
    "max_iterations": "max_iterations",
    "max_steps": "max_steps",
    "max_file_lines": "max_file_lines",
    "max_file_size_mb": "max_file_size_mb",
    "max_chunk_lines": "max_chunk_lines",
    "max_snippet_length": "max_snippet_length",
    "max_tree_depth": "max_tree_depth",
    "max_tree_files": "max_tree_files",
    "max_context_rounds": "max_context_rounds",
    "max_tool_output": "max_tool_output",
    "maxTokens": "maxTokens",
}

# CLI 参数名 → AgentConfig 字段名
CLI_TO_FIELD = {
    "api_key": "api_key",
    "model": "model_name",
    "base_url": "base_url",
    "root": "root_path",
    "max_iterations": "max_iterations",
    "max_file_lines": "max_file_lines",
    "max_file_size_mb": "max_file_size_mb",
    "max_chunk_lines": "max_chunk_lines",
    "max_snippet_length": "max_snippet_length",
    "max_tree_depth": "max_tree_depth",
    "max_tree_files": "max_tree_files",
    "max_steps": "max_steps",
    "max_context_rounds": "max_context_rounds",
    "max_tool_output": "max_tool_output",
    "maxTokens": "maxTokens",
}


# ============================================================
# 加载逻辑
# ============================================================

def _get_app_dir() -> Path:
    """获取应用目录：exe 同级目录（打包后）或项目根目录（开发时）。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.parent.resolve()


def _load_yaml_config(yaml_path: Path) -> dict:
    """加载 YAML 配置文件。"""
    if not yaml_path.exists():
        return {}
    if yaml is None:
        logging.warning("pyyaml 未安装，跳过 config.yaml 加载")
        return {}
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def build_config(args=None) -> AgentConfig:
    """
    构建 AgentConfig。

    优先级: CLI 参数 > config.yaml > 默认值
    
    配置文件查找顺序（conf/config.yaml）：
    1. exe 同级的 conf/ 目录（打包后分发用）
    2. 项目根目录的 conf/ 目录（开发时用）
    """
    # 1. 加载 config.yaml
    app_dir = _get_app_dir()
    yaml_data = _load_yaml_config(app_dir / "conf" / "config.yaml")

    # 2. 从 yaml 填充
    kwargs = {}
    for yaml_key, field_name in YAML_TO_FIELD.items():
        value = yaml_data.get(yaml_key)
        if value is not None:
            kwargs[field_name] = value

    # 3. 环境变量覆盖（兼容性，可选）
    env_map = {
        "OPENAI_API_KEY": "api_key",
        "OPENAI_BASE_URL": "base_url",
        "OPENAI_MODEL": "model_name",
    }
    for env_key, field_name in env_map.items():
        env_val = os.getenv(env_key)
        if env_val is not None:
            kwargs[field_name] = env_val

    # 4. CLI 参数覆盖（最高优先级）
    if args is not None:
        for cli_name, field_name in CLI_TO_FIELD.items():
            cli_value = getattr(args, cli_name.replace("-", "_"), None)
            if cli_value is not None:
                kwargs[field_name] = cli_value
        kwargs["verbose"] = getattr(args, "verbose", False)
        kwargs["debug"] = getattr(args, "debug", False)
        kwargs["interactive"] = getattr(args, "interactive", False)
        kwargs["no_prescan"] = getattr(args, "no_prescan", False)
        kwargs["save_llm_log"] = getattr(args, "save_llm_log", False)
        kwargs["mcp"] = getattr(args, "mcp", False)
        kwargs["mcp_transport"] = getattr(args, "mcp_transport", "stdio")

    valid = set(AgentConfig.__dataclass_fields__)
    filtered = {k: v for k, v in kwargs.items() if k in valid}

    # 基本验证
    warnings = []
    if not filtered.get("api_key"):
        warnings.append("未提供 API Key，运行时将报错")
    for field in ("max_iterations", "max_steps", "max_file_lines", "max_chunk_lines",
                   "max_snippet_length", "max_tree_depth", "max_tree_files",
                   "max_context_rounds", "max_tool_output", "max_file_size_mb",
                   "maxTokens"):
        val = filtered.get(field)
        if val is not None and (not isinstance(val, int) or val < 1):
            warnings.append(f"{field} 应为正整数，当前值: {val}")
            filtered[field] = 1  # 修正为最小合法值
    if warnings:
        logging.warning("配置警告: %s", "; ".join(warnings))

    return AgentConfig(**filtered)


# ============================================================
# 兼容常量（供 graph.py / tools.py / utils.py import）
# ============================================================

_config = build_config()

DEFAULT_ROOT_PATH = _config.root_path
MAX_EXPLORATION_ITERATIONS = _config.max_iterations
MAX_FILE_LINES = _config.max_file_lines
MAX_FILE_SIZE_MB = _config.max_file_size_mb
MAX_CHUNK_LINES = _config.max_chunk_lines
MAX_SNIPPET_LENGTH = _config.max_snippet_length
MAX_TREE_DEPTH = _config.max_tree_depth
MAX_TREE_FILES = _config.max_tree_files
MAX_STEPS = _config.max_steps
MAX_CONTEXT_ROUNDS = _config.max_context_rounds
MAX_TOOL_OUTPUT_CHARS = _config.max_tool_output
MAX_CONTEXT_TOKENS = _config.maxTokens


__all__ = [
    "build_config", "AgentConfig",
    "DEFAULT_ROOT_PATH", "MAX_EXPLORATION_ITERATIONS",
    "MAX_FILE_LINES", "MAX_FILE_SIZE_MB", "MAX_CHUNK_LINES",
    "MAX_SNIPPET_LENGTH", "MAX_TREE_DEPTH", "MAX_TREE_FILES",
    "MAX_STEPS", "MAX_CONTEXT_ROUNDS", "MAX_TOOL_OUTPUT_CHARS",
    "MAX_CONTEXT_TOKENS",
]
