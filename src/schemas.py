"""LLM 结构化输出 Schema"""

from pydantic import BaseModel
from typing import List


class ExtractedSegment(BaseModel):
    """从文件中提取的单个相关段落"""
    start_line: int
    end_line: int
    reasoning: str


class ExtractResult(BaseModel):
    """LLM 提取结果列表"""
    matches: List[ExtractedSegment]
