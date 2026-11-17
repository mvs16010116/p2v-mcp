"""端到端链路测试：用 fixtures 真实素材跑通标准链路。

- 纯节拍链路（无视觉）
- 带 focus 的链路（视觉依赖缺失时走降级，不崩）
- 错误码传递（坏路径 → FILE_NOT_FOUND）
- 10 个工具注册与统一返回结构
真实渲染依赖 HyperFrames CLI，缺失时 skip 并写明原因，不 mock。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

import main
from src.contracts import ErrorCode, Selector
from src.service.pipeline import PipelineRequest, PipelineService

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "fixtures" / "audio" / "bgm_120bpm.wav"
GROUP_IMAGE = ROOT / "fixtures" / "images" / "group_10people.jpg"
REF_IMAGE = ROOT / "fixtures" / "images" / "person_ref.jpg"

_CORE_TOOL_NAMES = {
    "validate_assets", "extract_audio_beats", "build_timeline", "adjust_timeline",
    "generate_composition", "preview_project", "render_video",
    "analyze_subjects", "lock_subject", "generate_focus_path",
}
_EXTENDED_TOOL_NAMES = {
    "list_templates", "get_template", "validate_template", "get_detection_report",
    "get_text_selector_report", "get_beat_benchmark", "get_determinism_report",
    "get_license_summary", "diagnose_webgl", "inspect_composition",
}
_TOOL_NAMES = _CORE_TOOL_NAMES | _EXTENDED_TOOL_NAMES


def _make_service(tmp_path: Path) -> PipelineService:
    return PipelineService(
        workspace_root=tmp_path / "workspace", logs_dir=tmp_path / "logs"
    )


def _hyperframes_available() -> bool:
    if shutil.which("npx") is None:
        return False
    try:
        result = subprocess.run(
            ["npx", "--no-install", "hyperframes", "--version"],
            capture_output=True, timeout=30, shell=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def test_all_tools_registered() -> None:
    """20 个工具全部注册：核心 10（方案 §5/20）+ 扩展 10（生态/审计/诊断）。"""
    tools = asyncio.run(main.mcp.list_tools())
    assert len(tools) == 20
    assert {tool.name for tool in tools} == _TOOL_NAMES
    assert _CORE_TOOL_NAMES <= _TOOL_NAMES and _EXTENDED_TOOL_NAMES <= _TOOL_NAMES


def test_tool_returns_unified_shape_on_bad_path(tmp_path: Path) -> None:
    """工具返回统一 {ok,data,error}，坏路径映射 FILE_NOT_FOUND。"""
    response = main.validate_assets(str(tmp_path / "missing.wav"), [], 1.0)
    assert response["ok"] is False
    assert response["data"] is None
    assert response["error"]["code"] == ErrorCode.FILE_NOT_FOUND.value
    assert response["error"]["message"]


def test_pure_beat_pipeline(tmp_path: Path) -> None:
    """① 纯节拍链路：validate → beats → timeline → composition，无视觉。"""
    service = _make_service(tmp_path)
    result = service.run_pipeline(
        PipelineRequest(
            audio_path=str(AUDIO),
            image_paths=[str(GROUP_IMAGE)],
            texts=["群像开场"],
            min_duration=1.0,
            beat_group=2,
            fps=30,
            cleanup=True,
        )
    )
    assert result.ok, result.error
    data = result.data
    assert data is not None
    assert data["beats"]["bpm"] > 0
    assert len(data["beats"]["grouped_beats"]) >= 2
    assert data["timeline"]["clips"]
    assert data["timeline"]["clips"][0]["start"] == 0.0
    assert data["composition"]["project_dir"]
    assert data["focus_path"] is None
    assert any(hit["frame"] >= 0 for hit in data["timeline"]["beat_impacts"])


def test_focus_pipeline_degrades_without_vision_deps(tmp_path: Path) -> None:
    """② 带 focus 链路：视觉依赖缺失时降级为手工锚点，不崩。"""
    service = _make_service(tmp_path)
    result = service.run_pipeline(
        PipelineRequest(
            audio_path=str(AUDIO),
            image_paths=[str(GROUP_IMAGE)],
            texts=["锁定主体"],
            beat_group=2,
            use_vision=True,
            selector=Selector(type="index", value=0),
            cleanup=True,
        )
    )
    assert result.ok, result.error
    data = result.data
    assert data is not None
    assert data["timeline"]["clips"]
    if data["focus_path"] is None:
        assert any("降级" in warning for warning in data["warnings"])


def test_bad_audio_path_error_code(tmp_path: Path) -> None:
    """③ 错误码传递：坏音频路径一路传到 ToolResponse 的 FILE_NOT_FOUND。"""
    service = _make_service(tmp_path)
    result = service.run_pipeline(
        {"audio_path": str(tmp_path / "missing.wav"), "image_paths": []}
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == ErrorCode.FILE_NOT_FOUND


def test_lock_subject_index_selector_is_pure_compute() -> None:
    """index selector 为纯计算，无需 ML 依赖即可返回 focus 数据块。"""
    analysis = {
        "image": str(GROUP_IMAGE),
        "image_size": [1080, 1440],
        "subjects": [
            {"index": 0, "label": "person", "bbox": [0.1, 0.2, 0.3, 0.6], "confidence": 0.9}
        ],
        "count": 1,
        "artifacts": {},
    }
    response = main.lock_subject(analysis, {"type": "index", "value": 0}, 0.62, "contain")
    assert response["ok"] is True
    assert response["data"]["bbox"]
    assert response["data"]["scale"] > 0


def test_generate_focus_path_tool() -> None:
    """generate_focus_path 工具返回 scale/x/y 轨迹与 warnings。"""
    focus = {
        "mode": "subject",
        "bbox": [0.31, 0.18, 0.44, 0.61],
        "fill": 0.62,
        "fit": "contain",
    }
    clip = {"index": 0, "start": 0.0, "end": 1.0, "duration": 1.0, "image": "assets/img_000.jpg"}
    cov = {"w": 2531.25, "h": 1350.0, "x": -725.625, "y": 0.0}
    response = main.generate_focus_path(focus, clip, cov)
    assert response["ok"] is True
    assert response["data"]["scale"] > 0
    assert isinstance(response["data"]["warnings"], list)


@pytest.mark.skipif(
    not _hyperframes_available(),
    reason="HyperFrames CLI 未安装（npx --no-install hyperframes --version 失败），跳过真实渲染，不 mock",
)
def test_pipeline_with_real_render(tmp_path: Path) -> None:
    """CLI 可用时跑完整出片链路并校验 RenderResult。"""
    service = _make_service(tmp_path)
    output = tmp_path / "out.mp4"
    result = service.run_pipeline(
        PipelineRequest(
            audio_path=str(AUDIO),
            image_paths=[str(GROUP_IMAGE)],
            texts=["群像开场"],
            beat_group=2,
            do_render=True,
            output_mp4=str(output),
            cleanup=True,
        )
    )
    assert result.ok, result.error
    assert result.data is not None
    assert result.data["render"]["output_path"] == str(output)
    assert output.is_file()
