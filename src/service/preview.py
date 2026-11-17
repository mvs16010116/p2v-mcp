"""预览服务包装（方案 5.6）。

recon 的 HyperFramesBackend.preview 已实现线程内静态服务与 stop_preview，
本模块只做薄封装供 main.py / pipeline.py 调用，不重复实现 HTTP 服务。
"""
from __future__ import annotations

from src.backend.hyperframes_backend import HyperFramesBackend
from src.contracts import ToolResponse


def start_preview(backend: HyperFramesBackend, project_dir: str, port: int = 0) -> ToolResponse:
    """委托 backend.preview；port=0 由系统分配空闲端口。"""
    return backend.preview(project_dir=project_dir, port=port)


def stop_preview(backend: HyperFramesBackend) -> None:
    """关闭后台预览服务。"""
    backend.stop_preview()
