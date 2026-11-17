"""每个 Pydantic model 的合法 + 非法校验测试（CODING_STANDARDS 第 5 节）。

本模块不依赖 ffmpeg / Node / 浏览器，纯校验测试。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.contracts import (
    AssetValidation,
    AudioInfo,
    Backdrop,
    BeatAnalysis,
    BeatImpact,
    BboxCoords,
    CinematicFx,
    CinematicMask,
    Clip,
    CommandResult,
    ErrorCode,
    ErrorInfo,
    Focus,
    Grade,
    ImageInfo,
    ImageSpring,
    KenBurns,
    Light,
    MaskConfig,
    Parallax,
    RenderResult,
    Reveal,
    Selector,
    Shatter,
    TextAnim,
    Timeline,
    ToolResponse,
    TourTo,
)

VALID_CLIP = {
    "index": 0,
    "start": 0.21,
    "end": 1.05,
    "duration": 0.84,
    "image": "assets/img_001.jpg",
}
VALID_AUDIO = {"duration": 4.12, "sample_rate": 44100, "channels": 2, "codec": "mp3"}
VALID_IMAGE = {"path": "assets/img_001.jpg", "width": 1080, "height": 1440}

# (model, 合法 payload, 非法 payload)
CASES = [
    (MaskConfig, {"type": "circle", "duration": 0.32}, {"type": "circle", "duration": 0.9}),
    (ImageSpring, {"stiffness": 240, "damping": 17}, {"stiffness": 50, "damping": 17}),
    (TextAnim, {"type": "spring-up-fade"}, {"type": "bogus"}),
    (KenBurns, {"pivot_x": "50%"}, {"pivot_x": "center"}),
    (
        BeatImpact,
        {"t": 0.2, "frame": 6, "type": "flash", "intensity": 0.5, "duration": 0.12},
        {"t": -1.0, "frame": 6, "type": "flash", "intensity": 0.5, "duration": 0.12},
    ),
    (CinematicMask, {"mode": "irregular"}, {"mode": "bogus"}),
    (Shatter, {"cols": 6, "rows": 6}, {"cols": 10, "rows": 10}),
    (Reveal, {"type": "flash-in", "blur_px": 14}, {"ghosts": 99}),
    (Backdrop, {"type": "blur"}, {"blur_px": -1}),
    (Grade, {"preset": "teal-orange"}, {"vignette": 2.0}),
    (Light, {"sweep": True, "flare_at_beat": [0, 4]}, {"sweep": "not-a-bool"}),
    (CinematicFx, {}, {"mask": {"mode": "bogus"}}),
    (Selector, {"type": "text", "query": "穿红衣服的女人"}, {"type": "text", "image": "ref.jpg"}),
    (Parallax, {"enable": True, "bg_ratio": 0.35}, {"haze": 2.0}),
    (
        TourTo,
        {"subject_index": 7, "bbox": [0.70, 0.20, 0.82, 0.65]},
        {"subject_index": 7, "bbox": [0.70, 0.20, 0.10, 0.65]},
    ),
    (
        Focus,
        {
            "mode": "subject",
            "selector": {"type": "index", "value": 1},
            "bbox": [0.31, 0.18, 0.44, 0.61],
        },
        {"bbox": [0.31, 0.18, 0.44, 0.61], "fill": 2.0},
    ),
    (Clip, VALID_CLIP, {"index": 0, "start": 1.0, "end": 0.5, "duration": 0.5, "image": "a.jpg"}),
    (
        Timeline,
        {"task_id": "12345678-1234-4234-8234-123456789012", "total_duration": 2.0, "clips": [VALID_CLIP]},
        {"task_id": "t", "total_duration": 0.5, "clips": [VALID_CLIP]},
    ),
    (AudioInfo, VALID_AUDIO, {**VALID_AUDIO, "channels": 0}),
    (ImageInfo, VALID_IMAGE, {**VALID_IMAGE, "width": 0}),
    (
        AssetValidation,
        {"audio": VALID_AUDIO, "images": [VALID_IMAGE]},
        {"audio": VALID_AUDIO, "images": [{**VALID_IMAGE, "valid": "maybe"}]},
    ),
    (
        BeatAnalysis,
        {"bpm": 124.0, "audio_duration": 4.12, "confidence": "high"},
        {"bpm": 124.0, "confidence": "unknown"},
    ),
    (
        RenderResult,
        {
            "output_path": "out.mp4",
            "duration": 4.12,
            "resolution": "1080x1920",
            "fps": 30,
            "render_time_sec": 42,
            "log_path": "logs/render.log",
        },
        {
            "output_path": "out.mp4",
            "duration": 4.12,
            "resolution": "1080*1920",
            "fps": 30,
            "render_time_sec": 42,
            "log_path": "logs/render.log",
        },
    ),
    (ErrorInfo, {"code": "RENDER_TIMEOUT", "message": "timeout"}, {"code": "UNKNOWN_CODE"}),
    (ToolResponse, {"ok": True, "data": {}}, {"ok": True, "error": {"code": "RENDER_TIMEOUT"}}),
    (CommandResult, {"ok": True, "returncode": 0}, {"ok": True, "returncode": 0, "duration_sec": -1}),
]


@pytest.mark.parametrize(
    "model,payload",
    [pytest.param(m, v, id=m.__name__) for m, v, _ in CASES],
)
def test_valid_payload(model, payload) -> None:
    """合法 payload 必须能成功构造 model。"""
    assert model.model_validate(payload) is not None


@pytest.mark.parametrize(
    "model,payload",
    [pytest.param(m, i, id=m.__name__) for m, _, i in CASES],
)
def test_invalid_payload(model, payload) -> None:
    """非法 payload 必须被 Pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_error_codes_match_spec() -> None:
    """错误码必须与方案 6.5 节的 9 个 code 完全一致，不得多也不得少。"""
    assert {code.value for code in ErrorCode} == {
        "FILE_NOT_FOUND",
        "INVALID_AUDIO",
        "AUDIO_TOO_SHORT",
        "INVALID_IMAGE",
        "EMPTY_TIMELINE",
        "BINARY_NOT_FOUND",
        "BROWSER_LAUNCH_FAILED",
        "RENDER_TIMEOUT",
        "FFMPEG_MUX_FAILED",
    }
    assert len(ErrorCode) == 9


def test_tool_response_failure_shape() -> None:
    """ok=false 时 data 必须为 null 且 error 非 null。"""
    failure = ToolResponse.model_validate({"ok": False, "error": {"code": "RENDER_TIMEOUT"}})
    assert failure.error is not None
    assert failure.error.code == ErrorCode.RENDER_TIMEOUT
    assert failure.data is None
    with pytest.raises(ValidationError):
        ToolResponse.model_validate(
            {"ok": False, "data": {"x": 1}, "error": {"code": "RENDER_TIMEOUT"}}
        )


def test_selector_all_three_types() -> None:
    """三种锁定方式均合法，且字段互斥。"""
    for payload in (
        {"type": "text", "query": "穿红衣服的女人"},
        {"type": "reference", "image": "assets/ref.jpg"},
        {"type": "index", "value": 3},
    ):
        assert Selector.model_validate(payload).type in {"text", "reference", "index"}


def test_clip_duration_consistency() -> None:
    """duration 必须等于 end - start。"""
    bad = {**VALID_CLIP, "duration": 99.0}
    with pytest.raises(ValidationError):
        Clip.model_validate(bad)


def test_shatter_shard_budget_boundary() -> None:
    """碎片总数上限 36（6×6）。"""
    assert Shatter.model_validate({"cols": 6, "rows": 6}).cols == 6
    with pytest.raises(ValidationError):
        Shatter.model_validate({"cols": 6, "rows": 7})


def test_bbox_coords_are_normalized() -> None:
    """bbox 必须归一化到 0..1（类型别名通过 Focus 验证）。"""
    with pytest.raises(ValidationError):
        Focus.model_validate({"bbox": [-0.1, 0.2, 0.4, 0.6]})
    assert BboxCoords is not None
