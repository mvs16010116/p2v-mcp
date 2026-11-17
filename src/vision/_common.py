"""视觉层内部共用工具：依赖惰性加载与统一降级返回。

重依赖一律通过 import_or_raise 在函数内部导入，ImportError 统一映射成
ErrorCode.BINARY_NOT_FOUND（方案 20.1 降级契约），绝不进渲染循环。
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from src.contracts import ErrorCode, ErrorInfo, ToolResponse


class VisionDependencyError(RuntimeError):
    """可选依赖未安装、权重未配置或版本过低。"""

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}


def binary_not_found(message: str, detail: dict[str, Any] | None = None) -> ToolResponse:
    """构造统一的 BINARY_NOT_FOUND 失败返回（不抛异常，主流程不崩）。"""
    return ToolResponse(
        ok=False,
        data=None,
        error=ErrorInfo(code=ErrorCode.BINARY_NOT_FOUND, message=message, detail=detail or {}),
    )


def file_not_found(message: str, detail: dict[str, Any] | None = None) -> ToolResponse:
    return ToolResponse(
        ok=False,
        data=None,
        error=ErrorInfo(code=ErrorCode.FILE_NOT_FOUND, message=message, detail=detail or {}),
    )


def import_or_raise(module: str, hint: str) -> Any:
    """惰性导入模块；ImportError → VisionDependencyError → BINARY_NOT_FOUND。"""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise VisionDependencyError(
            f"缺少可选依赖 {module}，视觉分析层已降级（可走 R6 手工锚点）",
            {"module": module, "hint": hint, "reason": str(exc)},
        ) from exc


def require_env(name: str, hint: str) -> str:
    """读取本地权重/配置路径的环境变量；未配置即降级，不触发任何网络下载。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise VisionDependencyError(
            f"未配置 {name}，无法离线加载视觉模型",
            {"env_var": name, "hint": hint},
        )
    return raw


def require_checkpoint(name: str, hint: str) -> Path:
    """权重路径校验：既接受单文件（SAM/Grounding DINO），也接受目录（transformers）。"""
    path = Path(require_env(name, hint)).expanduser()
    if not path.exists():
        raise VisionDependencyError(
            f"{name} 指向的权重路径不存在：{path}",
            {"env_var": name, "path": str(path), "hint": hint},
        )
    return path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_image_size(path: str | Path) -> tuple[int, int]:
    """读取图片像素尺寸 (width, height)。PIL 也是惰性导入。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"图片不存在：{p}")
    image_module = import_or_raise("PIL.Image", "pip install Pillow")
    with image_module.open(p) as im:
        return int(im.size[0]), int(im.size[1])
