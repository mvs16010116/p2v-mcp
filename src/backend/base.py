"""渲染后端抽象接口（方案 2.2：后端必须可插拔）。

本模块只定义接口签名，不含任何渲染实现。具体实现（Hyperframes / Remotion）
由后续任务添加到同目录；上层工具链与后端无关。

铁律：所有层动画只挂一条 GSAP 时间线，由 renderAtTime(t) 确定性驱动；
本接口不暴露任何自驱动渲染语义。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.contracts import Timeline, ToolResponse


class RenderBackend(ABC):
    """渲染后端基类。子类必须实现 generate_composition / preview / render。"""

    @abstractmethod
    def generate_composition(
        self,
        timeline: Timeline,
        *,
        width: int,
        height: int,
        fps: int,
        audio_path: str,
        font_family: str,
        output_dir: str | None = None,
    ) -> ToolResponse:
        """把 timeline 填充为渲染工程：拷贝素材、生成模板快照（方案 5.5）。

        图片必须拷贝进工程目录，模板内只写相对路径；字体缺失降级并写 warnings。
        """
        ...

    @abstractmethod
    def preview(self, project_dir: str, port: int = 0) -> ToolResponse:
        """启动静态预览服务，支持逐帧 seek（方案 5.6）。port=0 表示自动分配。"""
        ...

    @abstractmethod
    def render(
        self,
        project_dir: str,
        composition_id: str,
        audio_path: str,
        output_mp4: str,
        *,
        concurrency: int = 4,
        timeout_sec: int = 600,
    ) -> ToolResponse:
        """确定性渲染出片并混入 BGM（方案 5.7 / 18.3）。

        产物必须通过三项校验：文件存在、时长误差 ≤ 1 帧、首帧非纯黑。
        超时返回 RENDER_TIMEOUT 并保留日志到 logs/。
        """
        ...
