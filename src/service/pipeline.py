"""标准调用链编排（方案第 12 章 + 第 20 章视觉可选插入）。

职责：串起 validate_assets → extract_audio_beats → [analyze_subjects →
lock_subject → generate_focus_path 可选] → build_timeline → adjust_timeline 可选
→ generate_composition → preview 可选 → render_video，并管理 task_id 生命周期
与 workspace 清理策略。只做编排，不重写 analyzer / timeline / backend / vision
的任何算法。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from src.analyzer.asset_validator import AssetValidationError, validate_assets
from src.analyzer.beat_detector import BeatDetectionError, detect_beats
from src.backend.hyperframes_backend import HyperFramesBackend
from src.contracts import (
    BeatAnalysis,
    ErrorCode,
    ErrorInfo,
    Focus,
    Selector,
    StrictModel,
    Timeline,
    ToolResponse,
)
from src.service.preview import start_preview
from src.timeline.adjuster import adjust_timeline
from src.timeline.builder import EffectSpec, TimelineBuildError, build_timeline
from src.utils.workspace import WorkspaceManager
from src.vision.focus_math import CoverRect, bboxToFocus, cover_rect
from src.vision.subject_analyzer import detect_subjects, lock_subject


def _ok(data: dict[str, Any]) -> ToolResponse:
    return ToolResponse(ok=True, data=data)


def _fail(code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> ToolResponse:
    return ToolResponse(ok=False, error=ErrorInfo(code=code, message=message, detail=detail or {}))


class PipelineRequest(StrictModel):
    """run_pipeline 的入参 DTO（模块内部，不是跨工具契约）。"""

    audio_path: str = Field(min_length=1)
    image_paths: list[str] = Field(default_factory=list)
    texts: list[str] = Field(default_factory=list)
    min_duration: float = Field(default=1.0, ge=0.0)
    beat_group: int = Field(default=1, ge=1)
    fps: int = Field(default=30, ge=1)
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    font_family: str = Field(default="Noto Sans SC", min_length=1)
    loop_assets: bool = True
    use_vision: bool = False
    selector: Selector | None = None
    offset: float | None = None
    times: list[float] | None = None
    do_preview: bool = False
    do_render: bool = False
    output_mp4: str | None = None
    cleanup: bool = False
    retain_hours: float = Field(default=24.0, ge=0.0)
    task_id: str | None = None


class PipelineService:
    """持有 backend / workspace，向 10 个 MCP 工具提供可复用步骤。"""

    def __init__(
        self,
        *,
        backend: HyperFramesBackend | None = None,
        workspace_root: str | Path = "workspace",
        logs_dir: str | Path = "logs",
    ) -> None:
        self.backend = backend or HyperFramesBackend(
            workspace_root=workspace_root, logs_dir=logs_dir
        )
        self.workspace = WorkspaceManager(workspace_root)
        self._active_task_id: str | None = None
        self._asset_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # task_id / workspace 生命周期
    # ------------------------------------------------------------------
    def ensure_task(self, task_id: str | None = None) -> str:
        """复用显式 task_id，否则沿用当前 task，再否则新建 uuid4 沙箱。"""
        if task_id:
            self._active_task_id = task_id
            return task_id
        if self._active_task_id is None:
            self._active_task_id = self.workspace.create_task()
        return self._active_task_id

    def prepare_images(self, image_paths: list[str], task_id: str | None = None) -> list[str]:
        """把外部素材拷贝进当前 task 沙箱，返回模板可用的相对路径。"""
        task = task_id or self.ensure_task()
        result: list[str] = []
        for index, path in enumerate(image_paths):
            if path in self._asset_map:
                result.append(self._asset_map[path])
                continue
            relative = self.workspace.copy_asset(task, path, index)
            self._asset_map[path] = relative
            result.append(relative)
        return result

    def cleanup_task(self, task_id: str | None = None) -> None:
        """显式清理单个 task；否则清理超过保留时长的历史 task。"""
        if task_id:
            self.workspace.cleanup(task_id)
            if self._active_task_id == task_id:
                self._active_task_id = None
        else:
            self.workspace.cleanup_expired(24.0)

    # ------------------------------------------------------------------
    # 各步骤（供 main.py 的独立工具直接调用）
    # ------------------------------------------------------------------
    def validate_assets_step(
        self, audio_path: str, image_paths: list[str], min_duration: float = 1.0
    ) -> ToolResponse:
        try:
            result = validate_assets(audio_path, image_paths, min_duration)
        except AssetValidationError as exc:
            return _fail(exc.code, exc.message)
        return _ok(result.model_dump(mode="json"))

    def extract_beats_step(
        self,
        audio_path: str,
        start_time: float = 0.0,
        end_time: float | None = None,
        beat_group: int = 1,
    ) -> ToolResponse:
        try:
            result = detect_beats(audio_path, start_time, end_time, beat_group)
        except BeatDetectionError as exc:
            return _fail(exc.code, exc.message)
        return _ok(result.model_dump(mode="json"))

    def analyze_subjects_step(
        self,
        image_path: str,
        detect: list[str] | None = None,
        with_depth: bool = False,
        output_dir: str | Path | None = None,
    ) -> ToolResponse:
        return detect_subjects(image_path, detect=detect, output_dir=output_dir)

    def lock_subject_step(
        self,
        analysis_json: Any,
        selector: Any,
        fill: float = 0.62,
        fit: Literal["contain", "cover"] = "contain",
    ) -> ToolResponse:
        try:
            return lock_subject(analysis_json, selector, fill, fit)
        except ValueError as exc:
            return _fail(ErrorCode.INVALID_IMAGE, str(exc))

    def generate_focus_path_step(
        self,
        focus: Any,
        timeline_clip: Any,
        cov: Any,
        *,
        stage_w: float,
        stage_h: float,
    ) -> ToolResponse:
        """把锁定框换算成 GSAP 可用的 scale/x/y 轨迹（方案 20.4）。"""
        try:
            focus_model = focus if isinstance(focus, Focus) else Focus.model_validate(focus)
            cov_model = cov if isinstance(cov, CoverRect) else CoverRect.model_validate(cov)
            if focus_model.bbox is None:
                raise ValueError("focus.bbox 不能为空")
            result = bboxToFocus(
                focus_model.bbox,
                cov_model,
                stage_w,
                stage_h,
                focus_model.fill,
                focus_model.fit,
            )
        except Exception as exc:
            return _fail(ErrorCode.INVALID_IMAGE, f"聚焦路径计算失败: {exc}")

        data: dict[str, Any] = {
            "scale": result.scale,
            "x": result.x,
            "y": result.y,
            "transform_origin": result.transform_origin,
            "subject_center": list(result.subject_center),
            "fill": result.fill,
            "warnings": result.warnings,
        }
        if isinstance(timeline_clip, dict):
            data["clip_index"] = timeline_clip.get("index")
        elif timeline_clip is not None:
            data["clip_index"] = timeline_clip.index
        return _ok(data)

    def build_timeline_step(
        self,
        grouped_beats: list[float],
        images: list[str],
        texts: list[str],
        effects: list[EffectSpec] | None,
        loop_assets: bool,
        *,
        fps: int = 30,
        audio_duration: float | None = None,
        task_id: str | None = None,
        raw_beats: list[float] | None = None,
    ) -> ToolResponse:
        try:
            timeline = build_timeline(
                grouped_beats,
                images,
                texts,
                effects,
                loop_assets,
                fps,
                audio_duration=audio_duration,
                task_id=task_id,
                raw_beats=raw_beats,
            )
        except TimelineBuildError as exc:
            return _fail(exc.code, exc.message)
        except ValueError as exc:
            return _fail(ErrorCode.EMPTY_TIMELINE, str(exc))
        return _ok(timeline.model_dump(mode="json"))

    def generate_composition_step(
        self,
        timeline: Timeline,
        backend: str,
        width: int,
        height: int,
        fps: int,
        audio_path: str,
        font_family: str,
        output_dir: str | None = None,
    ) -> ToolResponse:
        if backend != "hyperframes":
            return _fail(ErrorCode.BINARY_NOT_FOUND, f"后端 {backend} 尚未实现")
        return self.backend.generate_composition(
            timeline,
            width=width,
            height=height,
            fps=fps,
            audio_path=audio_path,
            font_family=font_family,
            output_dir=output_dir,
        )

    def render_step(
        self,
        project_dir: str,
        composition_id: str,
        audio_path: str,
        output_mp4: str,
        concurrency: int = 4,
        timeout_sec: int = 600,
        gpu_mode: Literal["auto", "on", "off"] = "auto",
        resolution: str | None = None,
    ) -> ToolResponse:
        return self.backend.render(
            project_dir,
            composition_id,
            audio_path,
            output_mp4,
            concurrency=concurrency,
            timeout_sec=timeout_sec,
            gpu_mode=gpu_mode,
            resolution=resolution,
        )

    # ------------------------------------------------------------------
    # 全链编排
    # ------------------------------------------------------------------
    @staticmethod
    def _focus_from_lock(data: dict[str, Any], selector: Selector | None) -> Focus:
        return Focus(
            mode="subject",
            selector=selector,
            subject_index=int(data["subject_index"]),
            bbox=list(data["bbox"]),
            fill=float(data["fill"]),
            fit=str(data["fit"]),
        )

    def run_pipeline(self, request: PipelineRequest | dict[str, Any]) -> ToolResponse:
        """按方案第 12 章顺序跑全链；视觉层失败时降级不崩。"""
        try:
            req = (
                request
                if isinstance(request, PipelineRequest)
                else PipelineRequest.model_validate(request)
            )
        except ValidationError as exc:
            return _fail(ErrorCode.FILE_NOT_FOUND, f"pipeline 参数校验失败: {exc}")

        warnings: list[str] = []
        task_id = self.ensure_task(req.task_id)

        validation = self.validate_assets_step(req.audio_path, req.image_paths, req.min_duration)
        if not validation.ok:
            return validation

        beats_resp = self.extract_beats_step(req.audio_path, beat_group=req.beat_group)
        if not beats_resp.ok:
            return beats_resp
        beats = BeatAnalysis.model_validate(beats_resp.data)

        relative_images = self.prepare_images(req.image_paths, task_id)

        effects: list[EffectSpec] | None = None
        focus_path: dict[str, Any] | None = None
        if req.use_vision and req.image_paths:
            analysis_dir = self.workspace.task_dir(task_id) / "analysis"
            analysis = self.analyze_subjects_step(
                req.image_paths[0], output_dir=analysis_dir
            )
            if req.selector is not None and analysis.ok:
                locked = self.lock_subject_step(analysis.data, req.selector, 0.62, "contain")
                if locked.ok:
                    focus = self._focus_from_lock(locked.data, req.selector)
                    effects = [EffectSpec(focus=focus)]
                    size = analysis.data.get("image_size") or [req.width, req.height]
                    cov: CoverRect = cover_rect(
                        float(size[0]), float(size[1]), float(req.width), float(req.height)
                    )
                    path_result = self.generate_focus_path_step(
                        focus, None, cov, stage_w=req.width, stage_h=req.height
                    )
                    if path_result.ok:
                        focus_path = path_result.data
                        warnings.extend(path_result.data.get("warnings", []))
                    else:
                        warnings.append(f"聚焦路径计算降级: {path_result.error.message}")
                else:
                    warnings.append(f"主体锁定失败，降级手工锚点: {locked.error.message}")
            else:
                reason = (
                    analysis.error.code.value
                    if analysis.error is not None
                    else "未提供 selector"
                )
                warnings.append(f"视觉分析不可用（{reason}），降级为 R6 手工锚点")

        timeline_resp = self.build_timeline_step(
            beats.grouped_beats,
            relative_images,
            req.texts,
            effects,
            req.loop_assets,
            fps=req.fps,
            audio_duration=beats.audio_duration,
            task_id=task_id,
            raw_beats=beats.raw_beats,
        )
        if not timeline_resp.ok:
            return timeline_resp
        timeline = Timeline.model_validate(timeline_resp.data)

        if req.offset is not None:
            timeline, summary = adjust_timeline(timeline, mode="offset", offset=req.offset)
            warnings.extend(summary.warnings)
        elif req.times is not None:
            timeline, summary = adjust_timeline(timeline, mode="set_times", times=req.times)
            warnings.extend(summary.warnings)

        composition = self.generate_composition_step(
            timeline,
            "hyperframes",
            req.width,
            req.height,
            req.fps,
            req.audio_path,
            req.font_family,
        )
        if not composition.ok:
            return composition
        project_dir = composition.data["project_dir"]

        preview: dict[str, Any] | None = None
        if req.do_preview:
            preview_resp = start_preview(self.backend, project_dir)
            if not preview_resp.ok:
                return preview_resp
            preview = preview_resp.data

        render: dict[str, Any] | None = None
        if req.do_render:
            output = req.output_mp4 or str(self.workspace.task_dir(task_id) / "out.mp4")
            render_resp = self.render_step(
                project_dir, task_id, req.audio_path, output
            )
            if not render_resp.ok:
                return render_resp
            render = render_resp.data

        if req.cleanup:
            self.workspace.cleanup(task_id)

        return _ok(
            {
                "task_id": task_id,
                "validation": validation.data,
                "beats": beats.model_dump(mode="json"),
                "timeline": timeline.model_dump(mode="json"),
                "composition": composition.data,
                "focus_path": focus_path,
                "preview": preview,
                "render": render,
                "warnings": warnings,
            }
        )
