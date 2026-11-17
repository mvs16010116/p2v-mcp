"""视觉分析层（可选 Sidecar，方案第 20 章）。

职责边界：
- 只在**离线分析阶段**运行 AI 模型；渲染循环内零推理（WAR_MAP 决策 #2 / #5）。
- 所有分析结果落盘为 Pydantic v2 model 可校验的 JSON / PNG；下游只读文件。
- 缺少重依赖或本地权重时，工具返回 ToolResponse(error.code=BINARY_NOT_FOUND)，
  主流程退化为 R6 手工锚点，不阻断出片。

本包导入时不加载 torch / transformers / SAM / Grounding DINO 等任何重依赖。
"""
from __future__ import annotations

from .focus_math import (
    FocusResult,
    CoverRect,
    bboxToFocus,
    bbox_to_focus,
    coverRect,
    cover_rect,
    derive_anchor_bbox,
    project_bbox,
)
from .models import ArtifactPaths, DepthResult, Subject, SubjectAnalysis

__all__ = [
    "ArtifactPaths",
    "CoverRect",
    "DepthResult",
    "FocusResult",
    "Subject",
    "SubjectAnalysis",
    "bboxToFocus",
    "bbox_to_focus",
    "coverRect",
    "cover_rect",
    "derive_anchor_bbox",
    "project_bbox",
]
