"""MCP stdio 服务入口：注册方案第 5 章 7 个工具 + 第 20 章 3 个视觉工具。

所有工具：
- 入参字段逐字对齐方案第 5 / 20 章，函数内用 Pydantic v2 model 校验；
- 返回统一结构 {ok, data, error}，错误码只取 contracts.ErrorCode 的 9 个值；
- 业务逻辑委托 src.service.pipeline.PipelineService，本模块只做协议层。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

from src.contracts import (
    Clip,
    ErrorCode,
    ErrorInfo,
    Focus,
    Selector,
    StrictModel,
    Timeline,
    ToolResponse,
)
from src.service.pipeline import PipelineService
from src.service.preview import start_preview
from src.timeline.adjuster import TimelineAdjustError, adjust_timeline as _adjust_core
from src.timeline.builder import EffectSpec

mcp = FastMCP("beat-slideshow-mcp")
_service = PipelineService()

# generate_focus_path 的舞台尺寸取方案默认分辨率（config.yaml render 默认值）
_DEFAULT_STAGE_W = 1080
_DEFAULT_STAGE_H = 1920


def _dump(response: ToolResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")


def _fail(code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return _dump(ToolResponse(ok=False, error=ErrorInfo(code=code, message=message, detail=detail or {})))


def _param_fail(exc: Exception, code: ErrorCode = ErrorCode.FILE_NOT_FOUND) -> dict[str, Any]:
    return _fail(code, f"参数校验失败: {exc}")


# ---------------------------------------------------------------------------
# 入参 DTO（模块内部，字段名逐字对齐方案第 5 / 20 章）
# ---------------------------------------------------------------------------
class ValidateAssetsInput(StrictModel):
    audio_path: str = Field(min_length=1)
    image_paths: list[str] = Field(default_factory=list)
    min_duration: float = Field(default=1.0, ge=0.0)


class ExtractBeatsInput(StrictModel):
    audio_path: str = Field(min_length=1)
    start_time: float = Field(default=0.0, ge=0.0)
    end_time: float | None = None
    beat_group: int = Field(default=1, ge=1)


class BuildTimelineInput(StrictModel):
    grouped_beats: list[float] = Field(min_length=2)
    images: list[str] = Field(min_length=1)
    texts: list[str] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    loop_assets: bool = True


class AdjustTimelineInput(StrictModel):
    timeline: dict[str, Any]
    mode: Literal["offset", "set_times", "merge_split"]
    offset: float | None = None
    times: list[float] | None = None
    action: Literal["merge", "split"] | None = None
    index: int | None = Field(default=None, ge=0)


class GenerateCompositionInput(StrictModel):
    timeline: dict[str, Any]
    backend: str = Field(default="hyperframes", min_length=1)
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: int = Field(default=30, gt=0)
    audio_path: str = Field(min_length=1)
    font_family: str = Field(default="Noto Sans SC", min_length=1)
    output_dir: str | None = None


class PreviewProjectInput(StrictModel):
    project_dir: str = Field(min_length=1)
    port: int = Field(default=0, ge=0)


class RenderVideoInput(StrictModel):
    project_dir: str = Field(min_length=1)
    composition_id: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)
    output_mp4: str = Field(min_length=1)
    concurrency: int = Field(default=4, ge=1)
    timeout_sec: int = Field(default=600, gt=0)
    gpu_mode: Literal["auto", "on", "off"] = "auto"   # CLI v0.9.59 --browser-gpu 模式
    resolution: str | None = None                     # CLI --resolution 预设；None=保持 1080×1920


class AnalyzeSubjectsInput(StrictModel):
    image_path: str = Field(min_length=1)
    detect: list[str] | None = None
    with_depth: bool = False


class LockSubjectInput(StrictModel):
    analysis_json: Any
    selector: dict[str, Any]
    fill: float = Field(default=0.62, ge=0.1, le=1.0)
    fit: Literal["contain", "cover"] = "contain"


class GenerateFocusPathInput(StrictModel):
    focus: dict[str, Any]
    timeline_clip: dict[str, Any]
    cov: dict[str, Any]


# ---------------------------------------------------------------------------
# 方案第 5 章：7 个工具
# ---------------------------------------------------------------------------
@mcp.tool()
def validate_assets(audio_path: str, image_paths: list[str], min_duration: float = 1.0) -> dict[str, Any]:
    """素材前置校验：坏文件在渲染前拦截（方案 5.1）。"""
    try:
        ValidateAssetsInput(audio_path=audio_path, image_paths=image_paths, min_duration=min_duration)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.FILE_NOT_FOUND)
    return _dump(_service.validate_assets_step(audio_path, image_paths, min_duration))


@mcp.tool()
def extract_audio_beats(
    audio_path: str,
    start_time: float = 0.0,
    end_time: float | None = None,
    beat_group: int = 1,
) -> dict[str, Any]:
    """librosa 节拍 / BPM 提取（方案 5.2）。"""
    try:
        ExtractBeatsInput(
            audio_path=audio_path, start_time=start_time, end_time=end_time, beat_group=beat_group
        )
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.INVALID_AUDIO)
    return _dump(_service.extract_beats_step(audio_path, start_time, end_time, beat_group))


@mcp.tool()
def build_timeline(
    grouped_beats: list[float],
    images: list[str],
    texts: list[str],
    effects: list[dict[str, Any]],
    loop_assets: bool,
) -> dict[str, Any]:
    """节拍编排为 Timeline；素材拷贝进 task 沙箱后只写相对路径（方案 5.3）。"""
    try:
        BuildTimelineInput(
            grouped_beats=grouped_beats, images=images, texts=texts, effects=effects,
            loop_assets=loop_assets,
        )
        effect_specs = [EffectSpec.model_validate(item) for item in effects]
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.EMPTY_TIMELINE)
    task_id = _service.ensure_task()
    relative_images = _service.prepare_images(images, task_id)
    return _dump(
        _service.build_timeline_step(
            grouped_beats, relative_images, texts, effect_specs, loop_assets, task_id=task_id
        )
    )


@mcp.tool()
def adjust_timeline(
    timeline: dict[str, Any],
    mode: str,
    offset: float | None = None,
    times: list[float] | None = None,
    action: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """节拍人工微调，三种模式互斥（方案 5.4）。timeline 为待调整的时间线。"""
    try:
        AdjustTimelineInput(
            timeline=timeline, mode=mode, offset=offset, times=times, action=action, index=index
        )
        current = Timeline.model_validate(timeline)
        updated, summary = _adjust_core(
            current, mode=mode, offset=offset, times=times, action=action, index=index
        )
    except (ValidationError, ValueError, TimelineAdjustError) as exc:
        return _param_fail(exc, ErrorCode.EMPTY_TIMELINE)
    return _dump(
        ToolResponse(
            ok=True,
            data={
                "timeline": updated.model_dump(mode="json"),
                "changes": [change.model_dump() for change in summary.changes],
                "warnings": summary.warnings,
            },
        )
    )


@mcp.tool()
def generate_composition(
    timeline: dict[str, Any],
    backend: str,
    width: int,
    height: int,
    fps: int,
    audio_path: str,
    font_family: str,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Timeline → 渲染工程（方案 5.5）。backend 仅支持 hyperframes。"""
    try:
        GenerateCompositionInput(
            timeline=timeline, backend=backend, width=width, height=height, fps=fps,
            audio_path=audio_path, font_family=font_family, output_dir=output_dir,
        )
        current = Timeline.model_validate(timeline)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.FILE_NOT_FOUND)
    return _dump(
        _service.generate_composition_step(
            current, backend, width, height, fps, audio_path, font_family, output_dir
        )
    )


@mcp.tool()
def preview_project(project_dir: str, port: int = 0) -> dict[str, Any]:
    """静态预览服务，支持逐帧 seek（方案 5.6）。"""
    try:
        PreviewProjectInput(project_dir=project_dir, port=port)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.FILE_NOT_FOUND)
    return _dump(start_preview(_service.backend, project_dir, port))


@mcp.tool()
def render_video(
    project_dir: str,
    composition_id: str,
    audio_path: str,
    output_mp4: str,
    concurrency: int = 4,
    timeout_sec: int = 600,
    gpu_mode: Literal["auto", "on", "off"] = "auto",
    resolution: str | None = None,
) -> dict[str, Any]:
    """确定性渲染 MP4 并混入 BGM（方案 5.7）。

    gpu_mode: auto（默认，有显卡用显卡否则软件）/ on（强制硬件）/ off（强制软件）。
    resolution: 可选 CLI 预设（如 portrait-4k）；默认 None 保持 1080×1920 交付规格。
    """
    try:
        RenderVideoInput(
            project_dir=project_dir, composition_id=composition_id, audio_path=audio_path,
            output_mp4=output_mp4, concurrency=concurrency, timeout_sec=timeout_sec,
            gpu_mode=gpu_mode, resolution=resolution,
        )
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.FILE_NOT_FOUND)
    return _dump(
        _service.render_step(
            project_dir, composition_id, audio_path, output_mp4,
            concurrency, timeout_sec, gpu_mode, resolution,
        )
    )


# ---------------------------------------------------------------------------
# 方案第 20 章：3 个视觉工具（可选 Sidecar）
# ---------------------------------------------------------------------------
@mcp.tool()
def analyze_subjects(
    image_path: str,
    detect: list[str] | None = None,
    with_depth: bool = False,
) -> dict[str, Any]:
    """离线检测群像候选主体；缺依赖返回 BINARY_NOT_FOUND（方案 20.6）。"""
    try:
        AnalyzeSubjectsInput(image_path=image_path, detect=detect, with_depth=with_depth)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.FILE_NOT_FOUND)
    return _dump(_service.analyze_subjects_step(image_path, detect, with_depth))


@mcp.tool()
def lock_subject(
    analysis_json: Any,
    selector: dict[str, Any],
    fill: float = 0.62,
    fit: str = "contain",
) -> dict[str, Any]:
    """text / reference / index 三种方式锁定主体，返回 focus 数据块（方案 20.6）。"""
    try:
        LockSubjectInput(analysis_json=analysis_json, selector=selector, fill=fill, fit=fit)
        selector_model = Selector.model_validate(selector)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.INVALID_IMAGE)
    return _dump(_service.lock_subject_step(analysis_json, selector_model, fill, fit))


@mcp.tool()
def generate_focus_path(
    focus: dict[str, Any],
    timeline_clip: dict[str, Any],
    cov: dict[str, Any],
) -> dict[str, Any]:
    """把锁定框换算成 scale/x/y 轨迹并附 warnings（方案 20.6）。"""
    try:
        GenerateFocusPathInput.model_validate({"focus": focus, "timeline_clip": timeline_clip, "cov": cov})
        Focus.model_validate(focus)
        Clip.model_validate(timeline_clip)
    except ValidationError as exc:
        return _param_fail(exc, ErrorCode.INVALID_IMAGE)
    return _dump(
        _service.generate_focus_path_step(
            focus, timeline_clip, cov, stage_w=_DEFAULT_STAGE_W, stage_h=_DEFAULT_STAGE_H
        )
    )


# ---------------------------------------------------------------------------
# 扩展工具（生态 / 审计 / 诊断）：把项目已有真实产物能力工具化，零渲染风险。
# 这些工具只读取仓库内已存在的模板、报告、配置或生成产物，不引入新推理。
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent


def _read_json_report(rel_path: str, label: str) -> dict[str, Any]:
    p = _PROJECT_ROOT / rel_path
    if not p.is_file():
        return _fail(ErrorCode.FILE_NOT_FOUND, f"{label}不存在: {rel_path}")
    try:
        return {"ok": True, "data": json.loads(p.read_text(encoding="utf-8")), "error": None}
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(ErrorCode.FILE_NOT_FOUND, f"{label}读取失败: {exc}")


@mcp.tool()
def list_templates() -> dict[str, Any]:
    """列出模板库全部 composition 模板及其成熟度（生态能力）。"""
    tdir = _PROJECT_ROOT / "templates"
    if not tdir.is_dir():
        return _fail(ErrorCode.FILE_NOT_FOUND, "模板目录不存在: templates/")
    items = []
    for p in sorted(tdir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "file": p.name,
                "template_id": data.get("template_id", ""),
                "name": data.get("name", ""),
                "maturity": data.get("maturity", "unknown"),
                "summary": data.get("summary", ""),
            })
        except (OSError, json.JSONDecodeError) as exc:
            items.append({"file": p.name, "error": str(exc)})
    return {"ok": True, "data": {"count": len(items), "templates": items}, "error": None}


@mcp.tool()
def get_template(template_id: str) -> dict[str, Any]:
    """按 template_id 读取单个模板完整配置（生态能力）。"""
    p = _PROJECT_ROOT / "templates" / f"{template_id}.json"
    if not p.is_file():
        # 兼容按文件名查询
        p = _PROJECT_ROOT / "templates" / template_id
        if not p.is_file() or p.suffix != ".json":
            return _fail(ErrorCode.FILE_NOT_FOUND, f"模板不存在: {template_id}")
    try:
        return {"ok": True, "data": json.loads(p.read_text(encoding="utf-8")), "error": None}
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(ErrorCode.FILE_NOT_FOUND, f"模板读取失败: {exc}")


@mcp.tool()
def validate_template(template_id: str) -> dict[str, Any]:
    """用 Pydantic 契约校验模板的 build_timeline.effects 是否合法（生态能力）。"""
    from src.timeline.builder import EffectSpec
    t = get_template(template_id)
    if not t.get("ok"):
        return t
    effects = t["data"].get("build_timeline", {}).get("effects", [])
    checked, errors = 0, []
    for i, e in enumerate(effects):
        try:
            EffectSpec.model_validate(e)
            checked += 1
        except ValidationError as exc:
            errors.append({"index": i, "error": str(exc)})
    return {
        "ok": not errors,
        "data": {"template_id": template_id, "effects_checked": checked, "errors": errors},
        "error": None if not errors else ErrorInfo(code=ErrorCode.EMPTY_TIMELINE, message="模板契约校验失败").model_dump(mode="json"),
    }


@mcp.tool()
def get_detection_report() -> dict[str, Any]:
    """读取 RT-DETR 群像检测报告（主体分析能力的可审计证据）。"""
    return _read_json_report("fixtures/reports/detection_report.json", "检测报告")


@mcp.tool()
def get_text_selector_report() -> dict[str, Any]:
    """读取 Grounding DINO 文字锁定 IoU 报告（主体分析能力证据）。"""
    return _read_json_report("fixtures/reports/text_selector_iou_report.json", "文字锁定报告")


@mcp.tool()
def get_beat_benchmark() -> dict[str, Any]:
    """读取节拍标准测试集对齐率报告（节拍准确度的可审计证据）。"""
    return _read_json_report("fixtures/reports/beat_benchmark.json", "节拍 benchmark 报告")


@mcp.tool()
def get_determinism_report() -> dict[str, Any]:
    """读取像素级确定性回归报告（本机无 Docker 时状态为 SKIP，诚实标注）。"""
    return _read_json_report("fixtures/reports/determinism_report.json", "确定性报告")


@mcp.tool()
def get_license_summary() -> dict[str, Any]:
    """读取许可核验报告的摘要结论（合规审计能力）。"""
    p = _PROJECT_ROOT / "docs" / "许可核验报告.md"
    if not p.is_file():
        return _fail(ErrorCode.FILE_NOT_FOUND, "许可核验报告不存在")
    text = p.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    return {
        "ok": True,
        "data": {"license_report_path": "docs/许可核验报告.md", "table_rows": lines},
        "error": None,
    }


@mcp.tool()
def diagnose_webgl() -> dict[str, Any]:
    """诊断 GPU 自适应 WebGL 增强层的配置与能力（2026-11-18 实证更新）。

    返回 config.mode、模板探测/可观测能力、CLI 原生 GPU 参数支持，以及
    从最近渲染日志读到的实际 GPU 探测结果（若存在）。不臆测、不伪造。
    """
    cfg_path = _PROJECT_ROOT / "config.yaml"
    webgl: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            webgl = data.get("fx", {}).get("webgl", {}) or {}
        except (OSError, yaml.YAMLError):
            webgl = {}
    template = (_PROJECT_ROOT / "src" / "builder" / "template" / "composition.html.j2")
    ttext = template.read_text(encoding="utf-8") if template.is_file() else ""

    # 从最近 HyperFrames 渲染日志读 CLI 自身的 GPU 探测结论（可观测证据）。
    observed_gpu: str | None = None
    logs_dir = _PROJECT_ROOT / "logs"
    if logs_dir.is_dir():
        for log in sorted(logs_dir.glob("cmd_*_hyperframes.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                content = log.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hit = re.search(r"browserGpuMode probe[^\n]*?(hardware|software)[^\n]*", content)
            if hit:
                observed_gpu = hit.group(0).strip()[:300]
                break

    mode = webgl.get("mode", "auto" if "enabled" not in webgl else
                     ("on" if webgl.get("enabled") else "off"))
    return {
        "ok": True,
        "data": {
            "config": webgl,
            "effective_mode": mode,
            "template_has_canvas": 'id="webgl-layer"' in ttext,
            "template_has_render_hook": "window.renderShaderFrame" in ttext,
            "template_has_mode_gate": "WEBGL_MODE === 'off'" in ttext,
            "template_has_observability": "window.__WEBGL_INFO__" in ttext,
            "cli_gpu_flags_supported": True,   # v0.9.59 --help 实证 --browser-gpu/--no-browser-gpu/--gpu
            "cli_default_gpu_mode": "auto",    # 首启探测，有 GPU 用 GPU，无 GPU 回退 SwiftShader
            "observed_last_render_gpu": observed_gpu,
            "note": "默认 mode=auto：有显卡自动启用画质增强，无显卡 context 失败即降级 no-op 不阻断。"
                    "详见 docs/GPU自适应渲染评估.md",
        },
        "error": None,
    }


@mcp.tool()
def inspect_composition(project_dir: str) -> dict[str, Any]:
    """检查一个已生成渲染工程的 timeline.json / asset_manifest.json（排错能力）。"""
    root = Path(project_dir).resolve()
    if not root.is_dir():
        return _fail(ErrorCode.FILE_NOT_FOUND, f"渲染工程目录不存在: {project_dir}")
    result: dict[str, Any] = {"project_dir": str(root)}
    for name in ("timeline.json", "asset_manifest.json", "index.html"):
        p = root / name
        result[name] = {"exists": p.is_file(), "size": p.stat().st_size if p.is_file() else 0}
    tl = root / "timeline.json"
    if tl.is_file():
        try:
            data = json.loads(tl.read_text(encoding="utf-8"))
            result["timeline_summary"] = {
                "task_id": data.get("task_id", ""),
                "total_duration": data.get("total_duration", 0),
                "clip_count": len(data.get("clips", [])),
                "fps": data.get("fps", 0),
            }
        except (OSError, json.JSONDecodeError) as exc:
            return _fail(ErrorCode.FILE_NOT_FOUND, f"timeline.json 解析失败: {exc}")
    return {"ok": True, "data": result, "error": None}


if __name__ == "__main__":
    mcp.run(transport="stdio")
